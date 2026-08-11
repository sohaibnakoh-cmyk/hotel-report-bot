import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
import psycopg2
import os

# تحديد مراحل المحادثة
USERNAME, PASSWORD = range(2)

# اتصال قاعدة البيانات (كمثال)
def get_db_connection():
    return psycopg2.connect(os.environ.get("DATABASE_URL"))

# 1. عند الضغط على start: رسالة ترحيبية وطلب اسم المستخدم
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_text = (
        f"مرحباً بك يا {user.first_name} في نظام إدارة معلومات الفنادق 🏨\n\n"
        "يرجى إدخال **اسم المستخدم** الخاص بك للمتابعة:"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")
    return USERNAME

# 2. استقبال اسم المستخدم وطلب كلمة المرور
async def receive_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['username'] = update.message.text
    await update.message.reply_text("شكراً. الآن يرجى إدخال **كلمة المرور**:")
    return PASSWORD

# 3. استقبال كلمة المرور والتحقق منها من قاعدة البيانات
async def receive_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text
    username = context.user_data.get('username')

    # التحقق من قاعدة البيانات (حيث يتم إنشاء الحسابات بمعرفة المدير)
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # افتراض أن جدول المستخدمين اسمه users ويحتوي على username و password و role
        cur.execute("SELECT role FROM users WHERE username = %s AND password = %s", (username, password))
        user_record = cur.fetchone()
    except Exception as e:
        user_record = None
    finally:
        cur.close()
        conn.close()

    if user_record:
        role = user_record[0]
        await update.message.reply_text(f"✅ تم تسجيل الدخول بنجاح! مرحباً بك (الصلاحية: {role}).")
        # هنا يمكنك توجيه المستخدم للقائمة الرئيسية حسب صلاحيته
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ اسم المستخدم أو كلمة المرور غير صحيحة.\nالرجاء إعادة المحاولة أو إرسال /start من جديد.")
        return ConversationHandler.END

# إلغاء العملية
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم إلغاء عملية تسجيل الدخول.")
    return ConversationHandler.END

# إعداد الـ Handler وإضافته لتطبيق البوت
def setup_login_handler(application: Application):
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_username)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_handler)
