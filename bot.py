import os
import logging
from datetime import datetime
from functools import wraps
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)
from werkzeug.security import generate_password_hash, check_password_hash

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

DEWAN, AKARAT, TQARER, AMN_AFRAD, AMN_ALAMLEN = (
    "DEWAN", "AKARAT", "TQARER", "AMN_AFRAD", "AMN_ALAMLEN"
)

# Conversation states
LOGIN_USERNAME, LOGIN_PASSWORD = range(2)
CREATE_USERNAME, CREATE_PASSWORD, CREATE_MODULE = range(2, 5)
DEWAN_KIND, DEWAN_SUBJECT, DEWAN_DETAILS = range(5, 8)
TENANT_NAME, TENANT_NATIONALITY, TENANT_PROPERTY, TENANT_PHONE, TENANT_NOTES = range(8, 13)
MIG_PROVINCE, MIG_ARAB, MIG_FOREIGN, MIG_STATUS = range(13, 17)
REP_REQUIRED, REP_COMPLETED, REP_IMPOSSIBLE, REP_NOTES = range(17, 21)
AFRAD_SESSIONS, AFRAD_NOTES = range(21, 23)
ALAMLEN_ROUNDS, ALAMLEN_LOCATION, ALAMLEN_NOTES = range(23, 26)
BROADCAST_TEXT = 26

def db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn=db(); cur=conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
      id SERIAL PRIMARY KEY,
      username VARCHAR(100) UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      module VARCHAR(30) NOT NULL,
      telegram_id BIGINT UNIQUE,
      enabled BOOLEAN NOT NULL DEFAULT TRUE,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sessions_log(
      id SERIAL PRIMARY KEY,
      user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
      username VARCHAR(100) NOT NULL,
      telegram_id BIGINT,
      login_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      logout_at TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS dewan(
      id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
      kind VARCHAR(20) NOT NULL, subject TEXT NOT NULL, details TEXT,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS tenants(
      id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
      name TEXT NOT NULL, nationality TEXT, property TEXT, phone TEXT, notes TEXT,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS migrants(
      id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
      province TEXT NOT NULL, arab_count INTEGER DEFAULT 0,
      foreign_count INTEGER DEFAULT 0, status_text TEXT,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS reports(
      id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
      required_count INTEGER DEFAULT 0, completed_count INTEGER DEFAULT 0,
      impossible_count INTEGER DEFAULT 0, notes TEXT,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS amn_afrad(
      id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
      sessions_count INTEGER DEFAULT 0, notes TEXT,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS amn_alamlen(
      id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
      rounds_count INTEGER DEFAULT 0, location TEXT NOT NULL, notes TEXT,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit(); cur.close(); conn.close()

def get_user(telegram_id):
    conn=db(); cur=conn.cursor()
    cur.execute("SELECT * FROM users WHERE telegram_id=%s", (telegram_id,))
    row=cur.fetchone(); cur.close(); conn.close()
    return row

def get_user_by_id(uid):
    conn=db(); cur=conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=%s", (uid,))
    row=cur.fetchone(); cur.close(); conn.close()
    return row

def is_admin(update):
    return update.effective_user and update.effective_user.id in ADMIN_IDS

def admin_only(func):
    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        if not is_admin(update):
            if update.callback_query:
                await update.callback_query.answer("غير مصرح", show_alert=True)
            else:
                await update.message.reply_text("غير مصرح.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

WELCOME_TEXT = """بِسْمِ اللهِ الرَّحْمَنِ الرَّحِيمِ

﴿فَإِنَّ مَعَ الْعُسْرِ يُسْرًا ۝ إِنَّ مَعَ الْعُسْرِ يُسْرًا﴾

قال رسول الله ﷺ:
«إنما الأعمال بالنيات، وإنما لكل امرئ ما نوى»

اجعل نيتك طيبة، وقلبك مطمئنًا، وخطوتك مباركة.
لا تستصغر أثر الكلمة الطيبة، ولا المعروف الصغير؛
فرب عملٍ بسيطٍ تخلص فيه، يترك أثرًا كبيرًا.

نسأل الله أن يفتح لكم أبواب الخير،
ويكتب لكم التوفيق والسداد،
ويجعل أيامكم عامرةً بالطمأنينة والبركة."""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_TEXT)
    if is_admin(update):
        await manager_menu(update, context)
        return ConversationHandler.END
    user=get_user(update.effective_user.id)
    if user and user["enabled"]:
        context.user_data["user_id"]=user["id"]
        await user_menu(update, context)
    else:
        await update.message.reply_text("للمتابعة أرسل /login")
    return ConversationHandler.END

async def login_start(update, context):
    await update.message.reply_text("أرسل اسم المستخدم:")
    return LOGIN_USERNAME

async def login_username(update, context):
    context.user_data["login_username"]=update.message.text.strip()
    await update.message.reply_text("أرسل كلمة المرور:")
    return LOGIN_PASSWORD

async def login_password(update, context):
    username=context.user_data.get("login_username")
    password=update.message.text
    conn=db(); cur=conn.cursor()
    cur.execute("SELECT * FROM users WHERE username=%s", (username,))
    user=cur.fetchone()
    if not user or not user["enabled"] or not check_password_hash(user["password_hash"], password):
        cur.close(); conn.close()
        await update.message.reply_text("بيانات الدخول غير صحيحة أو الحساب معطل.\nأرسل /login للمحاولة مجدداً.")
        return ConversationHandler.END

    # حساب واحد مرتبط بتيليغرام واحد في نفس الوقت
    cur.execute("UPDATE users SET telegram_id=NULL WHERE telegram_id=%s AND id<>%s",
                (update.effective_user.id, user["id"]))
    cur.execute("UPDATE users SET telegram_id=%s WHERE id=%s",
                (update.effective_user.id, user["id"]))
    cur.execute("""INSERT INTO sessions_log(user_id,username,telegram_id)
                   VALUES(%s,%s,%s)""",
                (user["id"], user["username"], update.effective_user.id))
    conn.commit(); cur.close(); conn.close()

    context.user_data["user_id"]=user["id"]
    await update.message.reply_text("تم تسجيل الدخول بنجاح.")
    await user_menu(update, context)
    return ConversationHandler.END

async def logout(update, context):
    uid=context.user_data.get("user_id")
    if uid:
        conn=db(); cur=conn.cursor()
        cur.execute("""UPDATE sessions_log SET logout_at=CURRENT_TIMESTAMP
                       WHERE id=(SELECT id FROM sessions_log
                       WHERE user_id=%s AND logout_at IS NULL
                       ORDER BY login_at DESC LIMIT 1)""",(uid,))
        cur.execute("UPDATE users SET telegram_id=NULL WHERE id=%s",(uid,))
        conn.commit(); cur.close(); conn.close()
    context.user_data.clear()
    await update.message.reply_text("تم تسجيل الخروج.")
    return ConversationHandler.END

async def manager_menu(update, context):
    text="🛠 لوحة المدير\n\nاختر العملية:"
    kb=[
      [InlineKeyboardButton("➕ إنشاء حساب", callback_data="m:create"),
       InlineKeyboardButton("👥 الحسابات", callback_data="m:users")],
      [InlineKeyboardButton("⛔ تعطيل/تفعيل", callback_data="m:toggle"),
       InlineKeyboardButton("📊 التقارير", callback_data="m:reports")],
      [InlineKeyboardButton("📢 تعميم", callback_data="m:broadcast"),
       InlineKeyboardButton("🕒 الجلسات", callback_data="m:sessions")]
    ]
    await send_menu(update,text,kb)

async def user_menu(update, context):
    user=get_user(update.effective_user.id)
    if not user or not user["enabled"]:
        await send_menu(update,"الحساب غير موجود أو معطل.",[])
        return
    labels={
      DEWAN:"📂 DEWAN",
      AKARAT:"🏢 AKARAT",
      TQARER:"📊 TQARER",
      AMN_AFRAD:"👮 AMN AFRAD",
      AMN_ALAMLEN:"🚔 AMN ALAMLEN"
    }
    kb=[]
    if user["module"]==DEWAN:
        kb=[[InlineKeyboardButton("📥 وارد",callback_data="u:dewan:in"),
             InlineKeyboardButton("📤 صادر",callback_data="u:dewan:out")]]
    elif user["module"]==AKARAT:
        kb=[[InlineKeyboardButton("👤 المستأجرين",callback_data="u:tenants")],
            [InlineKeyboardButton("🌍 عربي_أجنبي",callback_data="u:migrants")]]
    elif user["module"]==TQARER:
        kb=[[InlineKeyboardButton("📊 TQARER",callback_data="u:reports")]]
    elif user["module"]==AMN_AFRAD:
        kb=[[InlineKeyboardButton("👮 AMN AFRAD",callback_data="u:afrad")]]
    elif user["module"]==AMN_ALAMLEN:
        kb=[[InlineKeyboardButton("🚔 AMN ALAMLEN",callback_data="u:alamlen")]]
    kb.append([InlineKeyboardButton("🚪 تسجيل الخروج",callback_data="u:logout")])
    await send_menu(update, f"{labels[user['module']]}\nالمستخدم: {user['username']}", kb)

async def send_menu(update,text,kb):
    if update.callback_query:
        try: await update.callback_query.edit_message_text(text,reply_markup=InlineKeyboardMarkup(kb))
        except Exception: await update.callback_query.message.reply_text(text,reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text,reply_markup=InlineKeyboardMarkup(kb))

@admin_only
async def admin_create_start(update,context):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("أرسل اسم المستخدم الجديد:")
    return CREATE_USERNAME

async def admin_create_username(update,context):
    context.user_data["new_username"]=update.message.text.strip()
    await update.message.reply_text("أرسل كلمة المرور:")
    return CREATE_PASSWORD

async def admin_create_password(update,context):
    context.user_data["new_password"]=update.message.text
    kb=[[InlineKeyboardButton(x,callback_data=f"create_module:{x}")] for x in [DEWAN,AKARAT,TQARER,AMN_AFRAD,AMN_ALAMLEN]]
    await update.message.reply_text("اختر القسم:",reply_markup=InlineKeyboardMarkup(kb))
    return CREATE_MODULE

@admin_only
async def admin_create_module(update,context):
    q=update.callback_query; await q.answer()
    module=q.data.split(":",1)[1]
    username=context.user_data["new_username"]; password=context.user_data["new_password"]
    conn=db(); cur=conn.cursor()
    try:
        cur.execute("""INSERT INTO users(username,password_hash,module)
                       VALUES(%s,%s,%s)""",
                    (username,generate_password_hash(password),module))
        conn.commit(); msg=f"✅ تم إنشاء الحساب\n\nالمستخدم: {username}\nالقسم: {module}"
    except psycopg2.errors.UniqueViolation:
        conn.rollback(); msg="❌ اسم المستخدم موجود مسبقاً."
    finally:
        cur.close(); conn.close()
    context.user_data.pop("new_username",None); context.user_data.pop("new_password",None)
    await q.message.reply_text(msg)
    await manager_menu(update,context)
    return ConversationHandler.END

@admin_only
async def admin_users(update,context):
    q=update.callback_query; await q.answer()
    conn=db(); cur=conn.cursor()
    cur.execute("SELECT id,username,module,enabled FROM users ORDER BY module,username")
    rows=cur.fetchall(); cur.close(); conn.close()
    if not rows:
        await q.message.reply_text("لا توجد حسابات.")
        return
    kb=[]
    for r in rows:
        status="🟢" if r["enabled"] else "🔴"
        kb.append([InlineKeyboardButton(
            f"{status} {r['username']} — {r['module']}",
            callback_data=f"m:user:{r['id']}")])
    await q.message.reply_text("الحسابات:",reply_markup=InlineKeyboardMarkup(kb))

@admin_only
async def admin_user_action(update,context):
    q=update.callback_query; await q.answer()
    uid=int(q.data.split(":")[2]); u=get_user_by_id(uid)
    if not u: return
    kb=[
      [InlineKeyboardButton("⛔ تعطيل/تفعيل",callback_data=f"m:toggleone:{uid}")],
      [InlineKeyboardButton("📊 التقرير اليومي",callback_data=f"m:reportone:{uid}")]
    ]
    await q.message.reply_text(
        f"المستخدم: {u['username']}\nالقسم: {u['module']}\nالحالة: {'فعال' if u['enabled'] else 'معطل'}",
        reply_markup=InlineKeyboardMarkup(kb))

@admin_only
async def admin_toggle_one(update,context):
    q=update.callback_query; await q.answer()
    uid=int(q.data.split(":")[2])
    conn=db(); cur=conn.cursor()
    cur.execute("UPDATE users SET enabled=NOT enabled WHERE id=%s",(uid,))
    conn.commit(); cur.close(); conn.close()
    await q.message.reply_text("تم تغيير حالة الحساب.")
    await admin_users(update,context)

@admin_only
async def admin_reports(update,context):
    q=update.callback_query; await q.answer()
    conn=db(); cur=conn.cursor()
    cur.execute("SELECT id,username,module FROM users ORDER BY username")
    rows=cur.fetchall(); cur.close(); conn.close()
    kb=[[InlineKeyboardButton(f"📊 {r['username']} — {r['module']}",callback_data=f"m:reportone:{r['id']}")] for r in rows]
    await q.message.reply_text("اختر الحساب:",reply_markup=InlineKeyboardMarkup(kb))

@admin_only
async def admin_report_one(update,context):
    q=update.callback_query; await q.answer()
    uid=int(q.data.split(":")[2]); u=get_user_by_id(uid)
    conn=db(); cur=conn.cursor()
    cur.execute("SELECT COUNT(*) c FROM sessions_log WHERE user_id=%s AND login_at::date=CURRENT_DATE",(uid,))
    logins=cur.fetchone()["c"]
    table={"DEWAN":"dewan","AKARAT":"tenants","TQARER":"reports","AMN_AFRAD":"amn_afrad","AMN_ALAMLEN":"amn_alamlen"}[u["module"]]
    cur.execute(f"SELECT COUNT(*) c FROM {table} WHERE user_id=%s AND created_at::date=CURRENT_DATE",(uid,))
    entries=cur.fetchone()["c"]; cur.close(); conn.close()
    await q.message.reply_text(
        f"📊 التقرير اليومي\n\nالمستخدم: {u['username']}\nالقسم: {u['module']}\n"
        f"جلسات الدخول اليوم: {logins}\nالبيانات المدخلة اليوم: {entries}")

@admin_only
async def admin_sessions(update,context):
    q=update.callback_query; await q.answer()
    conn=db(); cur=conn.cursor()
    cur.execute("""SELECT username,login_at,logout_at FROM sessions_log
                   ORDER BY login_at DESC LIMIT 50""")
    rows=cur.fetchall(); cur.close(); conn.close()
    if not rows:
        await q.message.reply_text("لا توجد جلسات.")
        return
    text="🕒 آخر الجلسات:\n\n"
    for r in rows:
        text += f"👤 {r['username']}\nدخول: {r['login_at']}\nخروج: {r['logout_at'] or 'مفتوحة'}\n\n"
    await q.message.reply_text(text)

@admin_only
async def broadcast_start(update, context):
    q=update.callback_query
    await q.answer()
    await q.message.reply_text(
        "📢 التعميم\n\n"
        "أرسل الآن نص الرسالة التي تريد إرسالها إلى جميع المستخدمين الفعّالين "
        "المرتبطين بحسابات Telegram.\n\n"
        "سيتم إرسالها للمستخدمين فقط ولن تُرسل للمديرين."
    )
    return BROADCAST_TEXT

@admin_only
async def broadcast_send(update, context):
    text=update.message.text
    conn=db()
    cur=conn.cursor()
    cur.execute("""
        SELECT username, telegram_id
        FROM users
        WHERE enabled=TRUE AND telegram_id IS NOT NULL
    """)
    users=cur.fetchall()
    cur.close()
    conn.close()

    sent=0
    failed=0
    for user in users:
        try:
            await context.bot.send_message(chat_id=user["telegram_id"], text=text)
            sent += 1
        except Exception as exc:
            failed += 1
            log.warning("Broadcast failed for user %s: %s", user["username"], exc)

    await update.message.reply_text(
        "✅ تم تنفيذ التعميم.\n\n"
        f"تم الإرسال بنجاح: {sent}\n"
        f"تعذر الإرسال: {failed}"
    )
    await manager_menu(update, context)
    return ConversationHandler.END

async def user_callback(update,context):
    q=update.callback_query; await q.answer()
    if not get_user(update.effective_user.id):
        await q.message.reply_text("يجب تسجيل الدخول عبر /login")
        return
    action=q.data
    if action=="u:logout":
        await logout(update,context); return
    if action.startswith("u:dewan:"):
        context.user_data["dewan_kind"]=action.split(":")[2]
        await q.message.reply_text("أرسل موضوع الوارد/الصادر:")
        return DEWAN_SUBJECT
    if action=="u:tenants":
        await q.message.reply_text("أرسل اسم المستأجر:")
        return TENANT_NAME
    if action=="u:migrants":
        await q.message.reply_text("أرسل اسم المحافظة (باللغة الأجنبية):")
        return MIG_PROVINCE
    if action=="u:reports":
        await q.message.reply_text("أرسل العدد المطلوب:")
        return REP_REQUIRED
    if action=="u:afrad":
        await q.message.reply_text("أرسل عدد الجلسات المنجزة:")
        return AFRAD_SESSIONS
    if action=="u:alamlen":
        await q.message.reply_text("أرسل عدد الجولات:")
        return ALAMLEN_ROUNDS
    return ConversationHandler.END

async def dewan_subject(update,context):
    context.user_data["dewan_subject"]=update.message.text
    await update.message.reply_text("أرسل التفاصيل:")
    return DEWAN_DETAILS

async def dewan_details(update,context):
    u=get_user(update.effective_user.id)
    conn=db(); cur=conn.cursor()
    cur.execute("INSERT INTO dewan(user_id,kind,subject,details) VALUES(%s,%s,%s,%s)",
                (u["id"],context.user_data["dewan_kind"],context.user_data["dewan_subject"],update.message.text))
    conn.commit(); cur.close(); conn.close()
    await update.message.reply_text("✅ تم حفظ البيان.")
    await user_menu(update,context); return ConversationHandler.END

async def tenant_name(update,context):
    context.user_data["tenant_name"]=update.message.text
    await update.message.reply_text("أرسل الجنسية:")
    return TENANT_NATIONALITY
async def tenant_nationality(update,context):
    context.user_data["tenant_nationality"]=update.message.text
    await update.message.reply_text("أرسل العقار:")
    return TENANT_PROPERTY
async def tenant_property(update,context):
    context.user_data["tenant_property"]=update.message.text
    await update.message.reply_text("أرسل رقم الهاتف:")
    return TENANT_PHONE
async def tenant_phone(update,context):
    context.user_data["tenant_phone"]=update.message.text
    await update.message.reply_text("أرسل الملاحظات أو اكتب -:")
    return TENANT_NOTES
async def tenant_notes(update,context):
    u=get_user(update.effective_user.id); conn=db(); cur=conn.cursor()
    cur.execute("""INSERT INTO tenants(user_id,name,nationality,property,phone,notes)
                   VALUES(%s,%s,%s,%s,%s,%s)""",
                (u["id"],context.user_data["tenant_name"],context.user_data["tenant_nationality"],
                 context.user_data["tenant_property"],context.user_data["tenant_phone"],update.message.text))
    conn.commit(); cur.close(); conn.close()
    await update.message.reply_text("✅ تم حفظ المستأجر.")
    await user_menu(update,context); return ConversationHandler.END

async def mig_province(update,context):
    context.user_data["mig_province"]=update.message.text
    await update.message.reply_text("أرسل عدد العرب:")
    return MIG_ARAB
async def mig_arab(update,context):
    context.user_data["mig_arab"]=int(update.message.text)
    await update.message.reply_text("أرسل عدد الأجانب:")
    return MIG_FOREIGN
async def mig_foreign(update,context):
    context.user_data["mig_foreign"]=int(update.message.text)
    await update.message.reply_text("أرسل الحالة:")
    return MIG_STATUS
async def mig_status(update,context):
    u=get_user(update.effective_user.id); conn=db(); cur=conn.cursor()
    cur.execute("""INSERT INTO migrants(user_id,province,arab_count,foreign_count,status_text)
                   VALUES(%s,%s,%s,%s,%s)""",
                (u["id"],context.user_data["mig_province"],context.user_data["mig_arab"],
                 context.user_data["mig_foreign"],update.message.text))
    conn.commit(); cur.close(); conn.close()
    await update.message.reply_text("✅ تم الحفظ.")
    await user_menu(update,context); return ConversationHandler.END

async def rep_required(update,context):
    context.user_data["required"]=int(update.message.text)
    await update.message.reply_text("أرسل العدد المنجز:")
    return REP_COMPLETED
async def rep_completed(update,context):
    context.user_data["completed"]=int(update.message.text)
    await update.message.reply_text("أرسل العدد المتعذر:")
    return REP_IMPOSSIBLE
async def rep_impossible(update,context):
    context.user_data["impossible"]=int(update.message.text)
    await update.message.reply_text("أرسل الملاحظات أو -:")
    return REP_NOTES
async def rep_notes(update,context):
    u=get_user(update.effective_user.id); conn=db(); cur=conn.cursor()
    cur.execute("""INSERT INTO reports(user_id,required_count,completed_count,impossible_count,notes)
                   VALUES(%s,%s,%s,%s,%s)""",
                (u["id"],context.user_data["required"],context.user_data["completed"],
                 context.user_data["impossible"],update.message.text))
    conn.commit(); cur.close(); conn.close()
    await update.message.reply_text("✅ تم حفظ التقرير.")
    await user_menu(update,context); return ConversationHandler.END

async def afrad_sessions(update,context):
    context.user_data["afrad_sessions"]=int(update.message.text)
    await update.message.reply_text("أرسل الملاحظات أو -:")
    return AFRAD_NOTES
async def afrad_notes(update,context):
    u=get_user(update.effective_user.id); conn=db(); cur=conn.cursor()
    cur.execute("INSERT INTO amn_afrad(user_id,sessions_count,notes) VALUES(%s,%s,%s)",
                (u["id"],context.user_data["afrad_sessions"],update.message.text))
    conn.commit(); cur.close(); conn.close()
    await update.message.reply_text("✅ تم حفظ البيانات.")
    await user_menu(update,context); return ConversationHandler.END

async def alamlen_rounds(update,context):
    context.user_data["rounds"]=int(update.message.text)
    await update.message.reply_text("أرسل مكان الجولة:")
    return ALAMLEN_LOCATION
async def alamlen_location(update,context):
    context.user_data["location"]=update.message.text
    await update.message.reply_text("أرسل الملاحظات أو -:")
    return ALAMLEN_NOTES
async def alamlen_notes(update,context):
    u=get_user(update.effective_user.id); conn=db(); cur=conn.cursor()
    cur.execute("""INSERT INTO amn_alamlen(user_id,rounds_count,location,notes)
                   VALUES(%s,%s,%s,%s)""",
                (u["id"],context.user_data["rounds"],context.user_data["location"],update.message.text))
    conn.commit(); cur.close(); conn.close()
    await update.message.reply_text("✅ تم حفظ بيانات الجولة.")
    await user_menu(update,context); return ConversationHandler.END

async def cancel(update,context):
    await update.message.reply_text("تم إلغاء العملية.")
    if is_admin(update): await manager_menu(update,context)
    else: await user_menu(update,context)
    return ConversationHandler.END

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        return


def start_health_server():
    # Render Web Services require a listening port. Telegram polling itself
    # does not open a port, so expose a tiny health endpoint for Render.
    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    log.info("Health server listening on port %s", port)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured in Render Environment Variables.")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured in Render Environment Variables.")

    start_health_server()
    init_db()
    app=Application.builder().token(BOT_TOKEN).build()

    login_conv=ConversationHandler(
      entry_points=[CommandHandler("login",login_start)],
      states={
        LOGIN_USERNAME:[MessageHandler(filters.TEXT & ~filters.COMMAND,login_username)],
        LOGIN_PASSWORD:[MessageHandler(filters.TEXT & ~filters.COMMAND,login_password)]
      }, fallbacks=[CommandHandler("cancel",cancel)]
    )

    create_conv=ConversationHandler(
      entry_points=[CallbackQueryHandler(admin_create_start,pattern=r"^m:create$")],
      states={
        CREATE_USERNAME:[MessageHandler(filters.TEXT & ~filters.COMMAND,admin_create_username)],
        CREATE_PASSWORD:[MessageHandler(filters.TEXT & ~filters.COMMAND,admin_create_password)],
        CREATE_MODULE:[CallbackQueryHandler(admin_create_module,pattern=r"^create_module:")]
      }, fallbacks=[CommandHandler("cancel",cancel)]
    )

    dewan_conv=ConversationHandler(
      entry_points=[CallbackQueryHandler(user_callback,pattern=r"^u:dewan:")],
      states={
        DEWAN_SUBJECT:[MessageHandler(filters.TEXT & ~filters.COMMAND,dewan_subject)],
        DEWAN_DETAILS:[MessageHandler(filters.TEXT & ~filters.COMMAND,dewan_details)]
      }, fallbacks=[CommandHandler("cancel",cancel)]
    )
    tenant_conv=ConversationHandler(
      entry_points=[CallbackQueryHandler(user_callback,pattern=r"^u:tenants$")],
      states={
        TENANT_NAME:[MessageHandler(filters.TEXT & ~filters.COMMAND,tenant_name)],
        TENANT_NATIONALITY:[MessageHandler(filters.TEXT & ~filters.COMMAND,tenant_nationality)],
        TENANT_PROPERTY:[MessageHandler(filters.TEXT & ~filters.COMMAND,tenant_property)],
        TENANT_PHONE:[MessageHandler(filters.TEXT & ~filters.COMMAND,tenant_phone)],
        TENANT_NOTES:[MessageHandler(filters.TEXT & ~filters.COMMAND,tenant_notes)]
      }, fallbacks=[CommandHandler("cancel",cancel)]
    )
    mig_conv=ConversationHandler(
      entry_points=[CallbackQueryHandler(user_callback,pattern=r"^u:migrants$")],
      states={
        MIG_PROVINCE:[MessageHandler(filters.TEXT & ~filters.COMMAND,mig_province)],
        MIG_ARAB:[MessageHandler(filters.TEXT & ~filters.COMMAND,mig_arab)],
        MIG_FOREIGN:[MessageHandler(filters.TEXT & ~filters.COMMAND,mig_foreign)],
        MIG_STATUS:[MessageHandler(filters.TEXT & ~filters.COMMAND,mig_status)]
      }, fallbacks=[CommandHandler("cancel",cancel)]
    )
    rep_conv=ConversationHandler(
      entry_points=[CallbackQueryHandler(user_callback,pattern=r"^u:reports$")],
      states={
        REP_REQUIRED:[MessageHandler(filters.TEXT & ~filters.COMMAND,rep_required)],
        REP_COMPLETED:[MessageHandler(filters.TEXT & ~filters.COMMAND,rep_completed)],
        REP_IMPOSSIBLE:[MessageHandler(filters.TEXT & ~filters.COMMAND,rep_impossible)],
        REP_NOTES:[MessageHandler(filters.TEXT & ~filters.COMMAND,rep_notes)]
      }, fallbacks=[CommandHandler("cancel",cancel)]
    )
    afrad_conv=ConversationHandler(
      entry_points=[CallbackQueryHandler(user_callback,pattern=r"^u:afrad$")],
      states={
        AFRAD_SESSIONS:[MessageHandler(filters.TEXT & ~filters.COMMAND,afrad_sessions)],
        AFRAD_NOTES:[MessageHandler(filters.TEXT & ~filters.COMMAND,afrad_notes)]
      }, fallbacks=[CommandHandler("cancel",cancel)]
    )
    alamlen_conv=ConversationHandler(
      entry_points=[CallbackQueryHandler(user_callback,pattern=r"^u:alamlen$")],
      states={
        ALAMLEN_ROUNDS:[MessageHandler(filters.TEXT & ~filters.COMMAND,alamlen_rounds)],
        ALAMLEN_LOCATION:[MessageHandler(filters.TEXT & ~filters.COMMAND,alamlen_location)],
        ALAMLEN_NOTES:[MessageHandler(filters.TEXT & ~filters.COMMAND,alamlen_notes)]
      }, fallbacks=[CommandHandler("cancel",cancel)]
    )

    app.add_handler(CommandHandler("start",start))
    app.add_handler(login_conv)
    app.add_handler(CommandHandler("logout",logout))
    app.add_handler(create_conv)

    broadcast_conv=ConversationHandler(
      entry_points=[CallbackQueryHandler(broadcast_start,pattern=r"^m:broadcast$")],
      states={
        BROADCAST_TEXT:[MessageHandler(filters.TEXT & ~filters.COMMAND,broadcast_send)]
      },
      fallbacks=[CommandHandler("cancel",cancel)]
    )
    app.add_handler(broadcast_conv)
    app.add_handler(dewan_conv)
    app.add_handler(tenant_conv)
    app.add_handler(mig_conv)
    app.add_handler(rep_conv)
    app.add_handler(afrad_conv)
    app.add_handler(alamlen_conv)

    app.add_handler(CallbackQueryHandler(admin_users,pattern=r"^m:users$"))
    app.add_handler(CallbackQueryHandler(admin_user_action,pattern=r"^m:user:\d+$"))
    app.add_handler(CallbackQueryHandler(admin_toggle_one,pattern=r"^m:toggleone:\d+$"))
    app.add_handler(CallbackQueryHandler(admin_reports,pattern=r"^m:reports$"))
    app.add_handler(CallbackQueryHandler(admin_report_one,pattern=r"^m:reportone:\d+$"))
    app.add_handler(CallbackQueryHandler(admin_sessions,pattern=r"^m:sessions$"))
    app.add_handler(CallbackQueryHandler(user_callback,pattern=r"^u:"))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__":
    main()
