import os
import logging
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from functools import wraps

import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)
from werkzeug.security import generate_password_hash, check_password_hash


# ============================================================
# إعدادات عامة
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("department_bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}


# ============================================================
# الأقسام الافتراضية
# ============================================================

DEFAULT_SECTIONS = [
    ("DEWAN", "📂 الديوان"),
    ("AKARAT", "🏢 العقارات"),
    ("TQARER", "📊 التقارير"),
    ("AMN_AFRAD", "👮 أمن الأفراد"),
    ("AMN_ALAMLEN", "🚔 أمن العاملين"),
]


# ============================================================
# حالات المحادثات
# ============================================================

(
    LOGIN_USERNAME,
    LOGIN_PASSWORD,

    CREATE_SECTION,
    CREATE_USERNAME,
    CREATE_PASSWORD,

    ADD_SECTION_NAME,
    ADD_SECTION_CODE,

    EDIT_ACCOUNT_SELECT,
    EDIT_ACCOUNT_PASSWORD,

    # الديوان
    DEWAN_KIND,
    DEWAN_RECIPIENT,
    DEWAN_BOOK_NO,
    DEWAN_REQUIRED,
    DEWAN_COMPLETED,
    DEWAN_NOT_COMPLETED,
    DEWAN_REASON,

    # العقارات
    TENANT_NAME,
    TENANT_NATIONALITY,
    TENANT_PROPERTY,
    TENANT_PHONE,
    TENANT_NOTES,

    # عربي / أجنبي
    MIG_PROVINCE,
    MIG_ARAB,
    MIG_FOREIGN,
    MIG_STATUS,

    # التقارير
    REP_REQUIRED,
    REP_COMPLETED,
    REP_IMPOSSIBLE,
    REP_NOTES,

    # أمن الأفراد
    AFRAD_SESSIONS,
    AFRAD_NOTES,

    # أمن العاملين
    ALAMLEN_ROUNDS,
    ALAMLEN_LOCATION,
    ALAMLEN_NOTES,

    # عام
    GENERIC_TEXT,

    # تعميم
    BROADCAST_TEXT,
) = range(37)


# ============================================================
# قاعدة البيانات
# ============================================================

def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL غير مضبوط في Render.")

    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=RealDictCursor,
        connect_timeout=15,
        sslmode="require",
    )


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sections (
            id SERIAL PRIMARY KEY,
            code VARCHAR(50) UNIQUE NOT NULL,
            name VARCHAR(150) NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            module VARCHAR(50) NOT NULL,
            telegram_id BIGINT UNIQUE,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sessions_log (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            username VARCHAR(100) NOT NULL,
            telegram_id BIGINT,
            login_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            logout_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS dewan (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            kind VARCHAR(20) NOT NULL,

            recipient TEXT NOT NULL DEFAULT '',
            book_number TEXT NOT NULL DEFAULT '',

            required_count INTEGER DEFAULT 0,
            completed_count INTEGER DEFAULT 0,
            not_completed_count INTEGER DEFAULT 0,

            reason TEXT,

            status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE',

            linked_dewan_id INTEGER REFERENCES dewan(id) ON DELETE SET NULL,

            sent_to_admin BOOLEAN NOT NULL DEFAULT FALSE,
            sent_at TIMESTAMP,

            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS tenants (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            nationality TEXT,
            property TEXT,
            phone TEXT,
            notes TEXT,
            sent_to_admin BOOLEAN NOT NULL DEFAULT FALSE,
            sent_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS migrants (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            province TEXT NOT NULL,
            arab_count INTEGER DEFAULT 0,
            foreign_count INTEGER DEFAULT 0,
            status_text TEXT,
            sent_to_admin BOOLEAN NOT NULL DEFAULT FALSE,
            sent_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS reports (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            required_count INTEGER DEFAULT 0,
            completed_count INTEGER DEFAULT 0,
            impossible_count INTEGER DEFAULT 0,
            notes TEXT,
            sent_to_admin BOOLEAN NOT NULL DEFAULT FALSE,
            sent_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS amn_afrad (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            sessions_count INTEGER DEFAULT 0,
            notes TEXT,
            sent_to_admin BOOLEAN NOT NULL DEFAULT FALSE,
            sent_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS amn_alamlen (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            rounds_count INTEGER DEFAULT 0,
            location TEXT NOT NULL,
            notes TEXT,
            sent_to_admin BOOLEAN NOT NULL DEFAULT FALSE,
            sent_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS generic_entries (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            module VARCHAR(50) NOT NULL,
            content TEXT NOT NULL,
            sent_to_admin BOOLEAN NOT NULL DEFAULT FALSE,
            sent_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # --------------------------------------------------------
    # تحديث جدول الديوان القديم
    # --------------------------------------------------------

    dewan_columns = [
        """
        ALTER TABLE dewan
        ADD COLUMN IF NOT EXISTS recipient TEXT
        """,
        """
        ALTER TABLE dewan
        ADD COLUMN IF NOT EXISTS book_number TEXT
        """,
        """
        ALTER TABLE dewan
        ADD COLUMN IF NOT EXISTS required_count INTEGER DEFAULT 0
        """,
        """
        ALTER TABLE dewan
        ADD COLUMN IF NOT EXISTS completed_count INTEGER DEFAULT 0
        """,
        """
        ALTER TABLE dewan
        ADD COLUMN IF NOT EXISTS not_completed_count INTEGER DEFAULT 0
        """,
        """
        ALTER TABLE dewan
        ADD COLUMN IF NOT EXISTS reason TEXT
        """,
        """
        ALTER TABLE dewan
        ADD COLUMN IF NOT EXISTS status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        """,
        """
        ALTER TABLE dewan
        ADD COLUMN IF NOT EXISTS linked_dewan_id INTEGER
        """,
        """
        ALTER TABLE dewan
        ADD COLUMN IF NOT EXISTS sent_to_admin BOOLEAN NOT NULL DEFAULT FALSE
        """,
        """
        ALTER TABLE dewan
        ADD COLUMN IF NOT EXISTS sent_at TIMESTAMP
        """,
    ]

    for query in dewan_columns:
        cur.execute(query)

    # --------------------------------------------------------
    # تحديث باقي الجداول القديمة
    # --------------------------------------------------------

    for table in (
        "tenants",
        "migrants",
        "reports",
        "amn_afrad",
        "amn_alamlen",
        "generic_entries",
    ):
        cur.execute(
            f"""
            ALTER TABLE {table}
            ADD COLUMN IF NOT EXISTS sent_to_admin
            BOOLEAN NOT NULL DEFAULT FALSE
            """
        )

        cur.execute(
            f"""
            ALTER TABLE {table}
            ADD COLUMN IF NOT EXISTS sent_at TIMESTAMP
            """
        )

    # --------------------------------------------------------
    # الأقسام الافتراضية
    # --------------------------------------------------------

    for code, name in DEFAULT_SECTIONS:
        cur.execute(
            """
            INSERT INTO sections(code, name)
            VALUES(%s, %s)
            ON CONFLICT(code) DO NOTHING
            """,
            (code, name),
        )

    conn.commit()
    cur.close()
    conn.close()

    log.info("Database initialized successfully.")


# ============================================================
# الاستعلامات
# ============================================================

def get_user(telegram_id):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            u.*,
            s.name AS section_name,
            s.enabled AS section_enabled
        FROM users u
        LEFT JOIN sections s ON s.code = u.module
        WHERE u.telegram_id=%s
        """,
        (telegram_id,),
    )

    row = cur.fetchone()

    cur.close()
    conn.close()

    return row


def get_user_by_id(uid):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            u.*,
            s.name AS section_name,
            s.enabled AS section_enabled
        FROM users u
        LEFT JOIN sections s ON s.code = u.module
        WHERE u.id=%s
        """,
        (uid,),
    )

    row = cur.fetchone()

    cur.close()
    conn.close()

    return row


def get_sections(enabled_only=True):
    conn = db()
    cur = conn.cursor()

    if enabled_only:
        cur.execute(
            """
            SELECT *
            FROM sections
            WHERE enabled=TRUE
            ORDER BY name
            """
        )
    else:
        cur.execute(
            """
            SELECT *
            FROM sections
            ORDER BY name
            """
        )

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return rows


def get_section(code):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM sections
        WHERE code=%s
        """,
        (code,),
    )

    row = cur.fetchone()

    cur.close()
    conn.close()

    return row


def is_admin(update):
    return bool(
        update.effective_user
        and update.effective_user.id in ADMIN_IDS
    )


def admin_only(func):
    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        if not is_admin(update):
            if update.callback_query:
                await update.callback_query.answer(
                    "غير مصرح لك.",
                    show_alert=True,
                )
            elif update.message:
                await update.message.reply_text(
                    "غير مصرح لك."
                )

            return

        return await func(update, context, *args, **kwargs)

    return wrapper


# ============================================================
# البداية
# ============================================================

WELCOME_TEXT = """بِسْمِ اللهِ الرَّحْمَنِ الرَّحِيمِ

﴿فَإِنَّ مَعَ الْعُسْرِ يُسْرًا ۝ إِنَّ مَعَ الْعُسْرِ يُسْرًا﴾

أهلًا وسهلًا بكم في نظام المتابعة والتقارير.

هذا النظام مخصص للمستخدمين الذين لديهم حساب معتمد.
بعد تسجيل الدخول ستظهر لك واجهة القسم المخصص لك،
ومن خلالها تستطيع إدخال البيانات وإرسالها إلى الإدارة.

نتمنى لكم التوفيق والسداد."""


def welcome_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔐 تسجيل الدخول",
                    callback_data="public:login",
                )
            ],
            [
                InlineKeyboardButton(
                    "ℹ️ معلومات النظام",
                    callback_data="public:info",
                )
            ],
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    if is_admin(update):
        await update.message.reply_text(
            WELCOME_TEXT,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🛠 لوحة المدير",
                            callback_data="admin:menu",
                        )
                    ]
                ]
            ),
        )
        return ConversationHandler.END

    await update.message.reply_text(
        WELCOME_TEXT,
        reply_markup=welcome_keyboard(),
    )

    return ConversationHandler.END


async def public_callback(update, context):
    q = update.callback_query
    await q.answer()

    if q.data == "public:info":
        await q.message.reply_text(
            "🔹 هذا النظام مخصص للمستخدمين الذين تم إنشاء حساباتهم من قبل الإدارة.\n"
            "🔹 إذا كان لديك اسم مستخدم وكلمة مرور اضغط على «تسجيل الدخول».\n"
            "🔹 إذا لم يكن لديك حساب، تواصل مع الإدارة."
        )
        return

    if q.data == "public:login":
        await q.message.reply_text(
            "🔐 أرسل اسم المستخدم:"
        )
        return LOGIN_USERNAME


# ============================================================
# أوامر البوت
# ============================================================

async def setup_bot_commands(application):
    await application.bot.set_my_commands(
        [
            ("start", "بدء تشغيل النظام"),
            ("login", "🔐 تسجيل الدخول"),
            ("logout", "🚪 تسجيل الخروج"),
        ]
    )


# ============================================================
# تسجيل الدخول
# ============================================================

async def login_start(update, context):
    await update.message.reply_text(
        "🔐 أرسل اسم المستخدم:"
    )
    return LOGIN_USERNAME


async def login_username(update, context):
    context.user_data["login_username"] = (
        update.message.text.strip()
    )

    await update.message.reply_text(
        "🔑 أرسل كلمة المرور:"
    )

    return LOGIN_PASSWORD


async def login_password(update, context):
    username = context.user_data.get(
        "login_username",
        "",
    ).strip()

    password = update.message.text or ""

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM users
        WHERE username=%s
        """,
        (username,),
    )

    user = cur.fetchone()

    if (
        not user
        or not user["enabled"]
        or not check_password_hash(
            user["password_hash"],
            password,
        )
    ):
        cur.close()
        conn.close()

        await update.message.reply_text(
            "❌ اسم المستخدم أو كلمة المرور غير صحيحة، "
            "أو أن الحساب معطل.\n\n"
            "اضغط /start للبدء."
        )

        return ConversationHandler.END

    # حساب واحد مرتبط بحساب Telegram واحد
    cur.execute(
        """
        UPDATE users
        SET telegram_id=NULL
        WHERE telegram_id=%s
          AND id<>%s
        """,
        (
            update.effective_user.id,
            user["id"],
        ),
    )

    cur.execute(
        """
        UPDATE users
        SET telegram_id=%s
        WHERE id=%s
        """,
        (
            update.effective_user.id,
            user["id"],
        ),
    )

    cur.execute(
        """
        INSERT INTO sessions_log(
            user_id,
            username,
            telegram_id
        )
        VALUES(%s,%s,%s)
        """,
        (
            user["id"],
            user["username"],
            update.effective_user.id,
        ),
    )

    conn.commit()

    cur.close()
    conn.close()

    context.user_data["user_id"] = user["id"]

    section = get_section(user["module"])

    section_name = (
        section["name"]
        if section
        else user["module"]
    )

    await update.message.reply_text(
        f"✅ تم تسجيل الدخول بنجاح.\n\n"
        f"👤 المستخدم: {user['username']}\n"
        f"📂 القسم: {section_name}\n\n"
        f"أهلًا وسهلًا بك في قسمك."
    )

    await user_menu(update, context)

    return ConversationHandler.END


async def logout(update, context):
    uid = context.user_data.get("user_id")

    if uid:
        conn = db()
        cur = conn.cursor()

        cur.execute(
            """
            UPDATE sessions_log
            SET logout_at=CURRENT_TIMESTAMP
            WHERE id=(
                SELECT id
                FROM sessions_log
                WHERE user_id=%s
                  AND logout_at IS NULL
                ORDER BY login_at DESC
                LIMIT 1
            )
            """,
            (uid,),
        )

        cur.execute(
            """
            UPDATE users
            SET telegram_id=NULL
            WHERE id=%s
            """,
            (uid,),
        )

        conn.commit()

        cur.close()
        conn.close()

    context.user_data.clear()

    if update.callback_query:
        await update.callback_query.answer()

        await update.callback_query.message.reply_text(
            "👋 تم تسجيل الخروج بنجاح.\n\n"
            "اضغط /start للبدء."
        )
    else:
        await update.message.reply_text(
            "👋 تم تسجيل الخروج بنجاح.\n\n"
            "اضغط /start للبدء."
        )

    return ConversationHandler.END


# ============================================================
# لوحة المدير
# ============================================================

async def manager_menu(update, context):
    text = (
        "🛠 لوحة الإدارة\n\n"
        "مرحبًا بك في لوحة التحكم.\n"
        "اختر العملية التي تريد تنفيذها:"
    )

    kb = [
        [
            InlineKeyboardButton(
                "➕ إنشاء حساب",
                callback_data="m:create",
            ),
            InlineKeyboardButton(
                "👥 الحسابات",
                callback_data="m:users",
            ),
        ],
        [
            InlineKeyboardButton(
                "✏️ تعديل حساب",
                callback_data="m:edit",
            ),
            InlineKeyboardButton(
                "📂 الأقسام",
                callback_data="m:sections",
            ),
        ],
        [
            InlineKeyboardButton(
                "⛔ تعطيل/تفعيل",
                callback_data="m:toggle",
            ),
            InlineKeyboardButton(
                "📊 التقارير",
                callback_data="m:reports",
            ),
        ],
        [
            InlineKeyboardButton(
                "📢 تعميم",
                callback_data="m:broadcast",
            ),
            InlineKeyboardButton(
                "🕒 الجلسات",
                callback_data="m:sessions",
            ),
        ],
    ]

    await send_menu(update, text, kb)


@admin_only
async def admin_menu_callback(update, context):
    await update.callback_query.answer()
    await manager_menu(update, context)


# ============================================================
# إنشاء الحساب
# ============================================================

@admin_only
async def admin_create_start(update, context):
    await update.callback_query.answer()

    sections = get_sections()

    if not sections:
        await update.callback_query.message.reply_text(
            "❌ لا توجد أقسام. أضف قسمًا أولًا."
        )
        return ConversationHandler.END

    kb = []

    for section in sections:
        kb.append(
            [
                InlineKeyboardButton(
                    section["name"],
                    callback_data=(
                        f"create_section:{section['code']}"
                    ),
                )
            ]
        )

    kb.append(
        [
            InlineKeyboardButton(
                "➕ إضافة قسم جديد",
                callback_data="m:add_section",
            )
        ]
    )

    await update.callback_query.message.reply_text(
        "📂 اختر القسم أولًا للحساب الجديد:",
        reply_markup=InlineKeyboardMarkup(kb),
    )

    return CREATE_SECTION


@admin_only
async def admin_create_section(update, context):
    q = update.callback_query
    await q.answer()

    code = q.data.split(":", 1)[1]

    section = get_section(code)

    if not section:
        await q.message.reply_text(
            "❌ القسم غير موجود."
        )
        return ConversationHandler.END

    context.user_data["new_module"] = section["code"]

    await q.message.reply_text(
        "👤 الآن أرسل اسم المستخدم الجديد:"
    )

    return CREATE_USERNAME


@admin_only
async def admin_create_username(update, context):
    username = update.message.text.strip()

    if len(username) < 3:
        await update.message.reply_text(
            "❌ اسم المستخدم يجب أن يكون "
            "3 أحرف/أرقام على الأقل."
        )
        return CREATE_USERNAME

    context.user_data["new_username"] = username

    await update.message.reply_text(
        "🔑 أرسل كلمة المرور:"
    )

    return CREATE_PASSWORD


@admin_only
async def admin_create_password(update, context):
    password = update.message.text or ""

    if len(password) < 4:
        await update.message.reply_text(
            "❌ كلمة المرور يجب أن تكون "
            "4 أحرف/أرقام على الأقل."
        )
        return CREATE_PASSWORD

    context.user_data["new_password"] = password

    username = context.user_data["new_username"]
    module = context.user_data["new_module"]

    section = get_section(module)

    conn = db()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            INSERT INTO users(
                username,
                password_hash,
                module
            )
            VALUES(%s,%s,%s)
            """,
            (
                username,
                generate_password_hash(password),
                module,
            ),
        )

        conn.commit()

        await update.message.reply_text(
            f"✅ تم إنشاء الحساب بنجاح.\n\n"
            f"👤 المستخدم: {username}\n"
            f"📂 القسم: "
            f"{section['name'] if section else module}\n"
            f"🔐 كلمة المرور: تم حفظها بشكل آمن."
        )

    except psycopg2.errors.UniqueViolation:
        conn.rollback()

        await update.message.reply_text(
            "❌ اسم المستخدم موجود مسبقًا. "
            "أرسل اسمًا آخر:"
        )

        cur.close()
        conn.close()

        return CREATE_USERNAME

    finally:
        cur.close()
        conn.close()

    context.user_data.pop("new_username", None)
    context.user_data.pop("new_password", None)
    context.user_data.pop("new_module", None)

    await manager_menu(update, context)

    return ConversationHandler.END


# ============================================================
# إضافة قسم
# ============================================================

@admin_only
async def add_section_start(update, context):
    q = update.callback_query
    await q.answer()

    await q.message.reply_text(
        "➕ إضافة قسم جديد\n\n"
        "أرسل اسم القسم الذي سيظهر للمستخدمين:"
    )

    return ADD_SECTION_NAME


@admin_only
async def add_section_name(update, context):
    name = update.message.text.strip()

    if not name:
        await update.message.reply_text(
            "❌ أرسل اسمًا صحيحًا للقسم:"
        )
        return ADD_SECTION_NAME

    context.user_data["new_section_name"] = name

    await update.message.reply_text(
        "أرسل رمز القسم بالإنجليزية بدون مسافات.\n\n"
        "مثال:\n"
        "LEGAL\n"
        "OPERATIONS\n"
        "ARCHIVE"
    )

    return ADD_SECTION_CODE


@admin_only
async def add_section_code(update, context):
    code = update.message.text.strip().upper()

    if not code or not code.replace("_", "").isalnum():
        await update.message.reply_text(
            "❌ الرمز يجب أن يحتوي على أحرف/أرقام "
            "وشرطة سفلية فقط.\n"
            "مثال: OPERATIONS"
        )
        return ADD_SECTION_CODE

    name = context.user_data["new_section_name"]

    conn = db()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            INSERT INTO sections(code, name)
            VALUES(%s,%s)
            """,
            (code, name),
        )

        conn.commit()

        await update.message.reply_text(
            f"✅ تمت إضافة القسم.\n\n"
            f"📂 الاسم: {name}\n"
            f"🔤 الرمز: {code}\n\n"
            f"يمكنك الآن إنشاء حساب واختيار هذا القسم."
        )

    except psycopg2.errors.UniqueViolation:
        conn.rollback()

        await update.message.reply_text(
            "❌ رمز القسم موجود مسبقًا. "
            "أرسل رمزًا آخر:"
        )

        cur.close()
        conn.close()

        return ADD_SECTION_CODE

    finally:
        cur.close()
        conn.close()

    context.user_data.pop(
        "new_section_name",
        None,
    )

    await manager_menu(update, context)

    return ConversationHandler.END


@admin_only
async def admin_sections(update, context):
    q = update.callback_query
    await q.answer()

    sections = get_sections(enabled_only=False)

    kb = []

    for s in sections:
        status = "🟢" if s["enabled"] else "🔴"

        kb.append(
            [
                InlineKeyboardButton(
                    f"{status} {s['name']} — {s['code']}",
                    callback_data=(
                        f"section_action:{s['id']}"
                    ),
                )
            ]
        )

    kb.append(
        [
            InlineKeyboardButton(
                "➕ إضافة قسم",
                callback_data="m:add_section",
            )
        ]
    )

    await q.message.reply_text(
        "📂 إدارة الأقسام:",
        reply_markup=InlineKeyboardMarkup(kb),
    )


# ============================================================
# تعديل الحساب
# ============================================================

@admin_only
async def admin_edit_start(update, context):
    q = update.callback_query
    await q.answer()

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, username, module, enabled
        FROM users
        ORDER BY username
        """
    )

    rows = cur.fetchall()

    cur.close()
    conn.close()

    if not rows:
        await q.message.reply_text(
            "لا توجد حسابات."
        )
        return ConversationHandler.END

    kb = []

    for r in rows:
        status = "🟢" if r["enabled"] else "🔴"

        kb.append(
            [
                InlineKeyboardButton(
                    f"{status} {r['username']}",
                    callback_data=(
                        f"edit_account:{r['id']}"
                    ),
                )
            ]
        )

    await q.message.reply_text(
        "✏️ اختر الحساب الذي تريد تعديله:",
        reply_markup=InlineKeyboardMarkup(kb),
    )

    return EDIT_ACCOUNT_SELECT


@admin_only
async def admin_edit_account_select(update, context):
    q = update.callback_query
    await q.answer()

    uid = int(q.data.split(":")[1])

    user = get_user_by_id(uid)

    if not user:
        await q.message.reply_text(
            "❌ الحساب غير موجود."
        )
        return ConversationHandler.END

    context.user_data["edit_user_id"] = uid

    section = (
        user["section_name"]
        or user["module"]
    )

    await q.message.reply_text(
        f"✏️ تعديل الحساب\n\n"
        f"👤 المستخدم: {user['username']}\n"
        f"📂 القسم: {section}\n\n"
        f"أرسل كلمة المرور الجديدة:"
    )

    return EDIT_ACCOUNT_PASSWORD


@admin_only
async def admin_edit_account_password(update, context):
    password = update.message.text or ""

    if len(password) < 4:
        await update.message.reply_text(
            "❌ كلمة المرور يجب أن تكون "
            "4 أحرف/أرقام على الأقل."
        )
        return EDIT_ACCOUNT_PASSWORD

    uid = context.user_data.get(
        "edit_user_id"
    )

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE users
        SET password_hash=%s
        WHERE id=%s
        """,
        (
            generate_password_hash(password),
            uid,
        ),
    )

    conn.commit()

    cur.close()
    conn.close()

    await update.message.reply_text(
        "✅ تم تغيير كلمة المرور بنجاح."
    )

    context.user_data.pop(
        "edit_user_id",
        None,
    )

    await manager_menu(update, context)

    return ConversationHandler.END


# ============================================================
# المستخدمون
# ============================================================

@admin_only
async def admin_users(update, context):
    q = update.callback_query
    await q.answer()

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            u.id,
            u.username,
            u.module,
            u.enabled,
            s.name AS section_name
        FROM users u
        LEFT JOIN sections s ON s.code=u.module
        ORDER BY s.name,u.username
        """
    )

    rows = cur.fetchall()

    cur.close()
    conn.close()

    if not rows:
        await q.message.reply_text(
            "لا توجد حسابات."
        )
        return

    kb = []

    for r in rows:
        status = "🟢" if r["enabled"] else "🔴"
        section = (
            r["section_name"]
            or r["module"]
        )

        kb.append(
            [
                InlineKeyboardButton(
                    f"{status} {r['username']} — {section}",
                    callback_data=(
                        f"m:user:{r['id']}"
                    ),
                )
            ]
        )

    await q.message.reply_text(
        "👥 الحسابات:",
        reply_markup=InlineKeyboardMarkup(kb),
    )


@admin_only
async def admin_user_action(update, context):
    q = update.callback_query
    await q.answer()

    uid = int(q.data.split(":")[2])

    u = get_user_by_id(uid)

    if not u:
        return

    section = (
        u["section_name"]
        or u["module"]
    )

    kb = [
        [
            InlineKeyboardButton(
                "🔐 تغيير كلمة المرور",
                callback_data=(
                    f"edit_account:{uid}"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "⛔ تعطيل/تفعيل",
                callback_data=(
                    f"m:toggleone:{uid}"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "📊 التقرير اليومي",
                callback_data=(
                    f"m:reportone:{uid}"
                ),
            )
        ],
    ]

    await q.message.reply_text(
        f"👤 الحساب: {u['username']}\n"
        f"📂 القسم: {section}\n"
        f"الحالة: "
        f"{'🟢 فعال' if u['enabled'] else '🔴 معطل'}",
        reply_markup=InlineKeyboardMarkup(kb),
    )


@admin_only
async def admin_toggle_one(update, context):
    q = update.callback_query
    await q.answer()

    uid = int(q.data.split(":")[2])

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE users
        SET enabled=NOT enabled
        WHERE id=%s
        RETURNING enabled
        """,
        (uid,),
    )

    row = cur.fetchone()

    if row and not row["enabled"]:
        cur.execute(
            """
            UPDATE users
            SET telegram_id=NULL
            WHERE id=%s
            """,
            (uid,),
        )

    conn.commit()

    cur.close()
    conn.close()

    await q.message.reply_text(
        "✅ تم تغيير حالة الحساب."
    )

    await admin_users(update, context)


# ============================================================
# تقارير المدير حسب الأقسام
# ============================================================

@admin_only
async def admin_reports(update, context):
    q = update.callback_query
    await q.answer()

    sections = get_sections(enabled_only=False)

    if not sections:
        await q.message.reply_text("❌ لا توجد أقسام.")
        return

    kb = []
    for section in sections:
        status = "🟢" if section["enabled"] else "🔴"
        kb.append([
            InlineKeyboardButton(
                f"{status} {section['name']}",
                callback_data=f"m:reportsection:{section['id']}",
            )
        ])

    await q.message.reply_text(
        "📊 تقارير الأقسام\n\nاختر القسم الذي تريد الاطلاع على تقريره:",
        reply_markup=InlineKeyboardMarkup(kb),
    )


@admin_only
async def admin_report_section(update, context):
    q = update.callback_query
    await q.answer()

    section_id = int(q.data.split(":")[2])
    section = None

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM sections WHERE id=%s", (section_id,))
    section = cur.fetchone()

    if not section:
        cur.close()
        conn.close()
        await q.message.reply_text("❌ القسم غير موجود.")
        return

    # المستخدمون ضمن القسم
    cur.execute("""
        SELECT id, username, enabled
        FROM users
        WHERE module=%s
        ORDER BY username
    """, (section["code"],))
    users = cur.fetchall()

    # ملخص البيانات المدخلة اليوم حسب المستخدم
    table_map = {
        "DEWAN": "dewan",
        "AKARAT": "tenants",
        "TQARER": "reports",
        "AMN_AFRAD": "amn_afrad",
        "AMN_ALAMLEN": "amn_alamlen",
    }
    table = table_map.get(section["code"], "generic_entries")

    user_stats = []
    for user in users:
        cur.execute("""
            SELECT COUNT(*) AS c
            FROM sessions_log
            WHERE user_id=%s AND login_at::date=CURRENT_DATE
        """, (user["id"],))
        logins = cur.fetchone()["c"]

        cur.execute(f"""
            SELECT COUNT(*) AS c
            FROM {table}
            WHERE user_id=%s AND created_at::date=CURRENT_DATE
        """, (user["id"],))
        entries = cur.fetchone()["c"]

        user_stats.append((user, logins, entries))

    # إجمالي القسم اليوم
    cur.execute(f"""
        SELECT COUNT(*) AS c
        FROM {table} t
        JOIN users u ON u.id=t.user_id
        WHERE u.module=%s AND t.created_at::date=CURRENT_DATE
    """, (section["code"],))
    total_entries = cur.fetchone()["c"]

    cur.execute("""
        SELECT COUNT(*) AS c
        FROM sessions_log sl
        JOIN users u ON u.id=sl.user_id
        WHERE u.module=%s AND sl.login_at::date=CURRENT_DATE
    """, (section["code"],))
    total_logins = cur.fetchone()["c"]

    cur.close()
    conn.close()

    text = (
        f"📊 <b>تقرير القسم</b>\n\n"
        f"📂 القسم: <b>{html.escape(section['name'])}</b>\n"
        f"📅 التاريخ: <b>{datetime.now().strftime('%Y-%m-%d')}</b>\n"
        f"🕒 إجمالي جلسات الدخول اليوم: <b>{total_logins}</b>\n"
        f"📝 إجمالي البيانات المدخلة اليوم: <b>{total_entries}</b>\n\n"
        f"👥 <b>المستخدمون:</b>\n"
    )

    if not user_stats:
        text += "لا يوجد مستخدمون في هذا القسم.\n"
    else:
        for user, logins, entries in user_stats:
            status = "🟢" if user["enabled"] else "🔴"
            text += (
                f"{status} {html.escape(user['username'])} — "
                f"دخول: {logins} | بيانات: {entries}\n"
            )

    # تفاصيل الديوان للقسم، لأن البريد جزء أساسي من تقرير الديوان
    if section["code"] == "DEWAN":
        conn = db()
        cur = conn.cursor()
        cur.execute("""
            SELECT d.kind, d.recipient, d.book_number,
                   d.required_count, d.completed_count,
                   d.not_completed_count, d.reason,
                   d.status, d.created_at, u.username
            FROM dewan d
            JOIN users u ON u.id=d.user_id
            WHERE u.module=%s AND d.created_at::date=CURRENT_DATE
            ORDER BY d.created_at DESC
            LIMIT 50
        """, (section["code"],))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        text += "\n📬 <b>حركة الديوان اليوم:</b>\n"
        if not rows:
            text += "لا توجد معاملات اليوم.\n"
        else:
            for r in rows:
                kind = "📥 وارد" if r["kind"] == "in" else "📤 صادر"
                text += (
                    f"{kind} | {html.escape(r['username'])} | "
                    f"الجهة: {html.escape(r['recipient'] or '-') } | "
                    f"رقم الكتاب: {html.escape(r['book_number'] or '-')} | "
                    f"المطلوب: {r['required_count']}"
                )
                if r["kind"] == "out":
                    text += (
                        f" | المنجز: {r['completed_count']}"
                        f" | غير المنجز: {r['not_completed_count']}"
                    )
                    if r["reason"] and r["reason"] != "-":
                        text += f" | السبب: {html.escape(r['reason'])}"
                if r["status"] == "TRANSFERRED":
                    text += " | 🔄 محوّل إلى الصادر"
                text += "\n"

    # Telegram يرفض الرسائل الطويلة جدًا، لذلك نقسم التقرير إلى أجزاء.
    chunks = [text[i:i+3800] for i in range(0, len(text), 3800)]
    for chunk in chunks:
        await q.message.reply_text(chunk, parse_mode="HTML")


@admin_only
async def admin_report_one(update, context):
    # إبقاء خيار التقرير الفردي من قائمة الحسابات، مع عرض تقرير حساب واحد.
    q = update.callback_query
    await q.answer()

    uid = int(q.data.split(":")[2])
    u = get_user_by_id(uid)
    if not u:
        await q.message.reply_text("❌ الحساب غير موجود.")
        return

    table_map = {
        "DEWAN": "dewan",
        "AKARAT": "tenants",
        "TQARER": "reports",
        "AMN_AFRAD": "amn_afrad",
        "AMN_ALAMLEN": "amn_alamlen",
    }
    table = table_map.get(u["module"], "generic_entries")

    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) AS c FROM sessions_log
        WHERE user_id=%s AND login_at::date=CURRENT_DATE
    """, (uid,))
    logins = cur.fetchone()["c"]

    cur.execute(f"""
        SELECT COUNT(*) AS c FROM {table}
        WHERE user_id=%s AND created_at::date=CURRENT_DATE
    """, (uid,))
    entries = cur.fetchone()["c"]
    cur.close()
    conn.close()

    await q.message.reply_text(
        f"📊 <b>التقرير اليومي للحساب</b>\n\n"
        f"👤 المستخدم: <b>{html.escape(u['username'])}</b>\n"
        f"📂 القسم: <b>{html.escape(u['section_name'] or u['module'])}</b>\n"
        f"🕒 جلسات الدخول اليوم: <b>{logins}</b>\n"
        f"📝 البيانات المدخلة اليوم: <b>{entries}</b>",
        parse_mode="HTML",
    )


# ============================================================
# التعميم
# ============================================================

@admin_only
async def broadcast_start(update, context):
    q = update.callback_query
    await q.answer()

    await q.message.reply_text(
        "📢 التعميم\n\n"
        "أرسل نص الرسالة الآن ليتم إرسالها "
        "إلى جميع المستخدمين الفعّالين "
        "المرتبطين بحسابات Telegram."
    )

    return BROADCAST_TEXT


@admin_only
async def broadcast_send(update, context):
    text = update.message.text

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT username,telegram_id
        FROM users
        WHERE enabled=TRUE
          AND telegram_id IS NOT NULL
        """
    )

    users = cur.fetchall()

    cur.close()
    conn.close()

    sent = 0
    failed = 0

    for user in users:
        try:
            await context.bot.send_message(
                chat_id=user["telegram_id"],
                text=text,
            )

            sent += 1

        except Exception as exc:
            failed += 1

            log.warning(
                "Broadcast failed for %s: %s",
                user["username"],
                exc,
            )

    await update.message.reply_text(
        "✅ تم تنفيذ التعميم.\n\n"
        f"تم الإرسال بنجاح: {sent}\n"
        f"تعذر الإرسال: {failed}"
    )

    await manager_menu(update, context)

    return ConversationHandler.END


# ============================================================
# قائمة المستخدم
# ============================================================

async def user_menu(update, context):
    user = get_user(
        update.effective_user.id
    )

    if not user or not user["enabled"]:
        await send_menu(
            update,
            "❌ الحساب غير موجود أو معطل.",
            [
                [
                    InlineKeyboardButton(
                        "🔐 تسجيل الدخول",
                        callback_data="public:login",
                    )
                ]
            ],
        )
        return

    section_name = (
        user["section_name"]
        or user["module"]
    )

    text = (
        f"🌟 أهلًا وسهلًا بك\n\n"
        f"👤 المستخدم: {user['username']}\n"
        f"📂 القسم: {section_name}\n\n"
        f"اختر العملية المطلوبة:"
    )

    kb = []

    if user["module"] == "DEWAN":

        kb.append(
            [
                InlineKeyboardButton(
                    "📥 وارد",
                    callback_data="u:dewan:in",
                ),
                InlineKeyboardButton(
                    "📤 صادر",
                    callback_data="u:dewan:out",
                ),
            ]
        )

    elif user["module"] == "AKARAT":

        kb.extend(
            [
                [
                    InlineKeyboardButton(
                        "👤 المستأجرين",
                        callback_data="u:tenants",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🌍 عربي / أجنبي",
                        callback_data="u:migrants",
                    )
                ],
            ]
        )

    elif user["module"] == "TQARER":

        kb.append(
            [
                InlineKeyboardButton(
                    "📊 التقرير اليومي",
                    callback_data="u:reports",
                )
            ]
        )

    elif user["module"] == "AMN_AFRAD":

        kb.append(
            [
                InlineKeyboardButton(
                    "👮 أمن الأفراد",
                    callback_data="u:afrad",
                )
            ]
        )

    elif user["module"] == "AMN_ALAMLEN":

        kb.append(
            [
                InlineKeyboardButton(
                    "🚔 أمن العاملين",
                    callback_data="u:alamlen",
                )
            ]
        )

    else:

        kb.append(
            [
                InlineKeyboardButton(
                    "📝 إدخال بيان",
                    callback_data="u:generic",
                )
            ]
        )

    kb.append(
        [
            InlineKeyboardButton(
                "🚪 تسجيل الخروج",
                callback_data="u:logout",
            )
        ]
    )

    await send_menu(
        update,
        text,
        kb,
    )


async def send_menu(update, text, kb):
    markup = InlineKeyboardMarkup(kb)

    if update.callback_query:

        try:
            await update.callback_query.edit_message_text(
                text,
                reply_markup=markup,
            )

        except Exception:

            await update.callback_query.message.reply_text(
                text,
                reply_markup=markup,
            )

    else:

        await update.message.reply_text(
            text,
            reply_markup=markup,
        )


# ============================================================
# إرسال البيانات للإدارة
# ============================================================

async def send_submission_to_admins(
    context,
    title,
    body,
    section_name,
    submission_kind="بيان",
):
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS is empty. Submission was not sent.")
        return 0

    safe_section = html.escape(str(section_name or "غير محدد"))
    safe_title = html.escape(str(title))
    safe_body = html.escape(str(body))

    message = (
        f"📨 <b>{html.escape(submission_kind)}</b>\n\n"
        f"📂 <b>القسم: {safe_section}</b>\n"
        f"📌 {safe_title}\n\n"
        f"{safe_body}"
    )

    sent = 0
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=message,
                parse_mode="HTML",
            )
            sent += 1
        except Exception as exc:
            log.warning(
                "Could not send submission to admin %s: %s",
                admin_id,
                exc,
            )
    return sent


async def finish_question(
    update,
    context,
    title,
    body,
    table,
    row_id,
):
    user = get_user(update.effective_user.id)
    section_name = (user["section_name"] if user else None) or (user["module"] if user else "غير محدد")
    submission_kind = "بريد" if table == "dewan" else "تقرير"

    context.user_data["pending_submission"] = {
        "title": title,
        "body": body,
        "table": table,
        "row_id": row_id,
        "section_name": section_name,
        "submission_kind": submission_kind,
    }

    await update.message.reply_text(
        "✅ تم حفظ البيانات.\n\n"
        "هل انتهيت من إدخال هذا البيان؟",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ نعم",
                        callback_data="finish:yes",
                    ),
                    InlineKeyboardButton(
                        "❌ لا",
                        callback_data="finish:no",
                    ),
                ]
            ]
        ),
    )


async def finish_callback(update, context):
    q = update.callback_query

    try:
        await q.answer()
    except Exception:
        pass

    pending = context.user_data.get(
        "pending_submission"
    )

    if not pending:
        await q.message.reply_text(
            "⚠️ لا توجد عملية معلقة."
        )
        return

    # --------------------------------------------------------
    # لا
    # --------------------------------------------------------

    if q.data == "finish:no":

        context.user_data.pop(
            "pending_submission",
            None,
        )

        try:
            await q.edit_message_text(
                "↩️ تم إلغاء إرسال هذا البيان إلى الإدارة.\n\n"
                "يمكنك العودة إلى القائمة وإدخال بيان جديد."
            )
        except Exception:

            await q.message.reply_text(
                "↩️ تم إلغاء إرسال هذا البيان إلى الإدارة."
            )

        await user_menu(
            update,
            context,
        )

        return

    # --------------------------------------------------------
    # نعم
    # --------------------------------------------------------

    if q.data == "finish:yes":

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📨 إرسال إلى الإدارة",
                        callback_data="finish:send",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "↩️ إلغاء",
                        callback_data="finish:no",
                    )
                ],
            ]
        )

        try:
            await q.edit_message_text(
                "📋 تم تأكيد البيانات.\n\n"
                "هل تريد إرسالها إلى الإدارة؟",
                reply_markup=keyboard,
            )
        except Exception:

            await q.message.reply_text(
                "📋 تم تأكيد البيانات.\n\n"
                "هل تريد إرسالها إلى الإدارة؟",
                reply_markup=keyboard,
            )

        return

    # --------------------------------------------------------
    # إرسال
    # --------------------------------------------------------

    if q.data == "finish:send":

        sent = await send_submission_to_admins(
            context,
            pending["title"],
            pending["body"],
            pending.get("section_name", "غير محدد"),
            pending.get("submission_kind", "بيان"),
        )

        allowed_tables = {
            "dewan",
            "tenants",
            "migrants",
            "reports",
            "amn_afrad",
            "amn_alamlen",
            "generic_entries",
        }

        table = pending["table"]

        if table not in allowed_tables:
            await q.message.reply_text(
                "❌ حدث خطأ في اسم الجدول."
            )
            return

        conn = db()
        cur = conn.cursor()

        cur.execute(
            f"""
            UPDATE {table}
            SET
                sent_to_admin=TRUE,
                sent_at=CURRENT_TIMESTAMP
            WHERE id=%s
            """,
            (pending["row_id"],),
        )

        conn.commit()

        cur.close()
        conn.close()

        context.user_data.pop(
            "pending_submission",
            None,
        )

        if sent:

            try:
                await q.edit_message_text(
                    "📨 تم إرسال البيان إلى الإدارة بنجاح."
                )
            except Exception:

                await q.message.reply_text(
                    "📨 تم إرسال البيان إلى الإدارة بنجاح."
                )

        else:

            try:
                await q.edit_message_text(
                    "⚠️ تم حفظ البيان في قاعدة البيانات، "
                    "لكن تعذر إرساله إلى الإدارة.\n\n"
                    "تأكد من ADMIN_IDS."
                )
            except Exception:

                await q.message.reply_text(
                    "⚠️ تم حفظ البيان، لكن تعذر إرساله إلى الإدارة."
                )

        await user_menu(
            update,
            context,
        )


# ============================================================
# الديوان
# ============================================================

async def dewan_start(update, context):
    q = update.callback_query
    await q.answer()

    user = get_user(
        update.effective_user.id
    )

    if not user or not user["enabled"]:
        await q.message.reply_text(
            "🔐 يجب تسجيل الدخول أولًا.\n"
            "اضغط /start للبدء."
        )
        return ConversationHandler.END

    kind = q.data.split(":")[2]

    context.user_data["dewan_kind"] = kind

    if kind == "in":

        await q.message.reply_text(
            "📥 الوارد\n\n"
            "أرسل الجهة المرسل منها:"
        )

    else:

        await q.message.reply_text(
            "📤 الصادر\n\n"
            "أرسل الجهة المرسل إليها:"
        )

    return DEWAN_RECIPIENT


async def dewan_recipient(update, context):
    recipient = update.message.text.strip()

    if not recipient:
        await update.message.reply_text(
            "❌ يجب إدخال الجهة.\n"
            "أرسل الجهة مرة أخرى:"
        )
        return DEWAN_RECIPIENT

    context.user_data["dewan_recipient"] = recipient

    await update.message.reply_text(
        "📑 أرسل رقم الكتاب:"
    )

    return DEWAN_BOOK_NO


async def dewan_book_number(update, context):
    book_number = update.message.text.strip()

    if not book_number:
        await update.message.reply_text(
            "❌ يجب إدخال رقم الكتاب:"
        )
        return DEWAN_BOOK_NO

    context.user_data["dewan_book_number"] = book_number

    await update.message.reply_text(
        "🔢 أرسل العدد المطلوب:"
    )

    return DEWAN_REQUIRED


async def dewan_required(update, context):
    try:
        value = int(
            update.message.text.strip()
        )

        if value < 0:
            raise ValueError

    except ValueError:

        await update.message.reply_text(
            "❌ أرسل عددًا صحيحًا:"
        )
        return DEWAN_REQUIRED

    context.user_data["dewan_required"] = value

    kind = context.user_data["dewan_kind"]

    if kind == "in":

        await save_dewan_incoming(
            update,
            context,
        )

        return ConversationHandler.END

    await update.message.reply_text(
        "🔢 العدد المطلوب: "
        f"{value}\n\n"
        "أرسل العدد المنجز:"
    )

    return DEWAN_COMPLETED


async def dewan_completed(update, context):
    try:
        value = int(
            update.message.text.strip()
        )

        if value < 0:
            raise ValueError

    except ValueError:

        await update.message.reply_text(
            "❌ أرسل عددًا صحيحًا:"
        )
        return DEWAN_COMPLETED

    context.user_data["dewan_completed"] = value

    await update.message.reply_text(
        "🔴 أرسل العدد غير المنجز:"
    )

    return DEWAN_NOT_COMPLETED


async def dewan_not_completed(update, context):
    try:
        value = int(
            update.message.text.strip()
        )

        if value < 0:
            raise ValueError

    except ValueError:

        await update.message.reply_text(
            "❌ أرسل عددًا صحيحًا:"
        )
        return DEWAN_NOT_COMPLETED

    context.user_data[
        "dewan_not_completed"
    ] = value

    if value > 0:

        await update.message.reply_text(
            "📝 أرسل سبب عدم الإنجاز:"
        )

        return DEWAN_REASON

    context.user_data["dewan_reason"] = "-"

    await save_dewan_outgoing(
        update,
        context,
    )

    return ConversationHandler.END


async def dewan_reason(update, context):
    reason = update.message.text.strip()

    if not reason:
        await update.message.reply_text(
            "❌ أرسل سبب عدم الإنجاز:"
        )
        return DEWAN_REASON

    context.user_data["dewan_reason"] = reason

    await save_dewan_outgoing(
        update,
        context,
    )

    return ConversationHandler.END


# ============================================================
# حفظ الوارد
# ============================================================

async def save_dewan_incoming(update, context):
    u = get_user(
        update.effective_user.id
    )

    recipient = context.user_data[
        "dewan_recipient"
    ]

    book_number = context.user_data[
        "dewan_book_number"
    ]

    required = context.user_data[
        "dewan_required"
    ]

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO dewan(
            user_id,
            kind,
            recipient,
            book_number,
            required_count,
            completed_count,
            not_completed_count,
            reason,
            status
        )
        VALUES(
            %s,
            'in',
            %s,
            %s,
            %s,
            0,
            0,
            NULL,
            'ACTIVE'
        )
        RETURNING id
        """,
        (
            u["id"],
            recipient,
            book_number,
            required,
        ),
    )

    row_id = cur.fetchone()["id"]

    conn.commit()

    cur.close()
    conn.close()

    body = (
        f"👤 المستخدم: {u['username']}\n"
        f"📂 القسم: "
        f"{u['section_name'] or u['module']}\n\n"
        f"📥 النوع: وارد\n"
        f"📤 الجهة المرسل منها: {recipient}\n"
        f"📑 رقم الكتاب: {book_number}\n"
        f"🔢 العدد المطلوب: {required}"
    )

    await finish_question(
        update,
        context,
        "📥 الديوان — وارد",
        body,
        "dewan",
        row_id,
    )


# ============================================================
# حفظ الصادر وربط الوارد
# ============================================================

async def save_dewan_outgoing(update, context):
    u = get_user(
        update.effective_user.id
    )

    recipient = context.user_data[
        "dewan_recipient"
    ]

    book_number = context.user_data[
        "dewan_book_number"
    ]

    required = context.user_data[
        "dewan_required"
    ]

    completed = context.user_data[
        "dewan_completed"
    ]

    not_completed = context.user_data[
        "dewan_not_completed"
    ]

    reason = context.user_data.get(
        "dewan_reason",
        "-",
    )

    conn = db()
    cur = conn.cursor()

    # --------------------------------------------------------
    # إنشاء الصادر
    # --------------------------------------------------------

    cur.execute(
        """
        INSERT INTO dewan(
            user_id,
            kind,
            recipient,
            book_number,
            required_count,
            completed_count,
            not_completed_count,
            reason,
            status
        )
        VALUES(
            %s,
            'out',
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            'ACTIVE'
        )
        RETURNING id
        """,
        (
            u["id"],
            recipient,
            book_number,
            required,
            completed,
            not_completed,
            reason,
        ),
    )

    outgoing_id = cur.fetchone()["id"]

    # --------------------------------------------------------
    # البحث عن وارد فعال بنفس رقم الكتاب
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT id
        FROM dewan
        WHERE kind='in'
          AND book_number=%s
          AND status='ACTIVE'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (book_number,),
    )

    incoming = cur.fetchone()

    linked = False

    if incoming:

        incoming_id = incoming["id"]

        # إلغاء/تحويل الوارد
        cur.execute(
            """
            UPDATE dewan
            SET
                status='TRANSFERRED',
                linked_dewan_id=%s
            WHERE id=%s
            """,
            (
                outgoing_id,
                incoming_id,
            ),
        )

        # ربط الصادر بالوارد
        cur.execute(
            """
            UPDATE dewan
            SET linked_dewan_id=%s
            WHERE id=%s
            """,
            (
                incoming_id,
                outgoing_id,
            ),
        )

        linked = True

    conn.commit()

    cur.close()
    conn.close()

    body = (
        f"👤 المستخدم: {u['username']}\n"
        f"📂 القسم: "
        f"{u['section_name'] or u['module']}\n\n"
        f"📤 النوع: صادر\n"
        f"📥 الجهة المرسل إليها: {recipient}\n"
        f"📑 رقم الكتاب: {book_number}\n"
        f"🔢 العدد المطلوب: {required}\n"
        f"✅ العدد المنجز: {completed}\n"
        f"🔴 العدد غير المنجز: {not_completed}\n"
        f"📝 سبب عدم الإنجاز: {reason}\n"
    )

    if linked:

        body += (
            "\n🔄 تم العثور على وارد بنفس رقم الكتاب.\n"
            "تم تحويل الوارد إلى الصادر وربط المعاملتين تلقائيًا."
        )

    await finish_question(
        update,
        context,
        "📤 الديوان — صادر",
        body,
        "dewan",
        outgoing_id,
    )


# ============================================================
# العقارات
# ============================================================

async def tenant_name(update, context):
    context.user_data["tenant_name"] = (
        update.message.text
    )

    await update.message.reply_text(
        "🌍 أرسل الجنسية:"
    )

    return TENANT_NATIONALITY


async def tenant_nationality(update, context):
    context.user_data[
        "tenant_nationality"
    ] = update.message.text

    await update.message.reply_text(
        "🏠 أرسل العقار:"
    )

    return TENANT_PROPERTY


async def tenant_property(update, context):
    context.user_data[
        "tenant_property"
    ] = update.message.text

    await update.message.reply_text(
        "📞 أرسل رقم الهاتف:"
    )

    return TENANT_PHONE


async def tenant_phone(update, context):
    context.user_data[
        "tenant_phone"
    ] = update.message.text

    await update.message.reply_text(
        "📝 أرسل الملاحظات أو اكتب -:"
    )

    return TENANT_NOTES


async def tenant_notes(update, context):
    u = get_user(
        update.effective_user.id
    )

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO tenants(
            user_id,
            name,
            nationality,
            property,
            phone,
            notes
        )
        VALUES(%s,%s,%s,%s,%s,%s)
        RETURNING id
        """,
        (
            u["id"],
            context.user_data["tenant_name"],
            context.user_data["tenant_nationality"],
            context.user_data["tenant_property"],
            context.user_data["tenant_phone"],
            update.message.text,
        ),
    )

    row_id = cur.fetchone()["id"]

    conn.commit()

    cur.close()
    conn.close()

    body = (
        f"👤 المستخدم: {u['username']}\n"
        f"📂 القسم: "
        f"{u['section_name'] or u['module']}\n"
        f"👤 المستأجر: "
        f"{context.user_data['tenant_name']}\n"
        f"🌍 الجنسية: "
        f"{context.user_data['tenant_nationality']}\n"
        f"🏠 العقار: "
        f"{context.user_data['tenant_property']}\n"
        f"📞 الهاتف: "
        f"{context.user_data['tenant_phone']}\n"
        f"📝 الملاحظات: "
        f"{update.message.text}"
    )

    await finish_question(
        update,
        context,
        "🏢 بيانات المستأجر",
        body,
        "tenants",
        row_id,
    )

    return ConversationHandler.END


# ============================================================
# عربي / أجنبي
# ============================================================

async def mig_province(update, context):
    context.user_data[
        "mig_province"
    ] = update.message.text

    await update.message.reply_text(
        "👥 أرسل عدد العرب:"
    )

    return MIG_ARAB


async def mig_arab(update, context):
    try:
        value = int(
            update.message.text.strip()
        )
    except ValueError:
        await update.message.reply_text(
            "❌ أرسل رقمًا صحيحًا:"
        )
        return MIG_ARAB

    context.user_data["mig_arab"] = value

    await update.message.reply_text(
        "🌍 أرسل عدد الأجانب:"
    )

    return MIG_FOREIGN


async def mig_foreign(update, context):
    try:
        value = int(
            update.message.text.strip()
        )
    except ValueError:
        await update.message.reply_text(
            "❌ أرسل رقمًا صحيحًا:"
        )
        return MIG_FOREIGN

    context.user_data[
        "mig_foreign"
    ] = value

    await update.message.reply_text(
        "📌 أرسل الحالة:"
    )

    return MIG_STATUS


async def mig_status(update, context):
    u = get_user(
        update.effective_user.id
    )

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO migrants(
            user_id,
            province,
            arab_count,
            foreign_count,
            status_text
        )
        VALUES(%s,%s,%s,%s,%s)
        RETURNING id
        """,
        (
            u["id"],
            context.user_data["mig_province"],
            context.user_data["mig_arab"],
            context.user_data["mig_foreign"],
            update.message.text,
        ),
    )

    row_id = cur.fetchone()["id"]

    conn.commit()

    cur.close()
    conn.close()

    body = (
        f"👤 المستخدم: {u['username']}\n"
        f"📂 القسم: "
        f"{u['section_name'] or u['module']}\n"
        f"📍 المحافظة: "
        f"{context.user_data['mig_province']}\n"
        f"👥 العرب: "
        f"{context.user_data['mig_arab']}\n"
        f"🌍 الأجانب: "
        f"{context.user_data['mig_foreign']}\n"
        f"📌 الحالة: "
        f"{update.message.text}"
    )

    await finish_question(
        update,
        context,
        "🌍 عربي / أجنبي",
        body,
        "migrants",
        row_id,
    )

    return ConversationHandler.END


# ============================================================
# التقارير اليومية
# ============================================================

async def rep_required(update, context):
    try:
        value = int(
            update.message.text.strip()
        )
    except ValueError:
        await update.message.reply_text(
            "❌ العدد المطلوب يجب أن يكون رقمًا.\n"
            "أرسل العدد:"
        )
        return REP_REQUIRED

    context.user_data["required"] = value

    await update.message.reply_text(
        "📌 العدد المطلوب:\n"
        f"{value}\n\n"
        "أرسل الآن العدد المنجز:"
    )

    return REP_COMPLETED


async def rep_completed(update, context):
    try:
        value = int(
            update.message.text.strip()
        )
    except ValueError:
        await update.message.reply_text(
            "❌ أرسل العدد المنجز كرقم:"
        )
        return REP_COMPLETED

    context.user_data["completed"] = value

    await update.message.reply_text(
        "✅ العدد المنجز:\n"
        f"{value}\n\n"
        "أرسل الآن العدد المتعذر:"
    )

    return REP_IMPOSSIBLE


async def rep_impossible(update, context):
    try:
        value = int(
            update.message.text.strip()
        )
    except ValueError:
        await update.message.reply_text(
            "❌ أرسل العدد المتعذر كرقم:"
        )
        return REP_IMPOSSIBLE

    context.user_data["impossible"] = value

    await update.message.reply_text(
        "⚠️ العدد المتعذر:\n"
        f"{value}\n\n"
        "أرسل الملاحظات أو اكتب -:"
    )

    return REP_NOTES


async def rep_notes(update, context):
    u = get_user(
        update.effective_user.id
    )

    required = context.user_data["required"]
    completed = context.user_data["completed"]
    impossible = context.user_data["impossible"]
    notes = update.message.text

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO reports(
            user_id,
            required_count,
            completed_count,
            impossible_count,
            notes
        )
        VALUES(%s,%s,%s,%s,%s)
        RETURNING id
        """,
        (
            u["id"],
            required,
            completed,
            impossible,
            notes,
        ),
    )

    row_id = cur.fetchone()["id"]

    conn.commit()

    cur.close()
    conn.close()

    body = (
        f"👤 المستخدم: {u['username']}\n"
        f"📂 القسم: "
        f"{u['section_name'] or u['module']}\n\n"
        f"📌 العدد المطلوب: {required}\n"
        f"✅ العدد المنجز: {completed}\n"
        f"⚠️ العدد المتعذر: {impossible}\n"
        f"📝 الملاحظات: {notes}"
    )

    await finish_question(
        update,
        context,
        "📊 التقرير اليومي",
        body,
        "reports",
        row_id,
    )

    return ConversationHandler.END


# ============================================================
# أمن الأفراد
# ============================================================

async def afrad_sessions(update, context):
    try:
        value = int(
            update.message.text.strip()
        )
    except ValueError:
        await update.message.reply_text(
            "❌ أرسل رقمًا صحيحًا:"
        )
        return AFRAD_SESSIONS

    context.user_data[
        "afrad_sessions"
    ] = value

    await update.message.reply_text(
        "📝 أرسل الملاحظات أو -:"
    )

    return AFRAD_NOTES


async def afrad_notes(update, context):
    u = get_user(
        update.effective_user.id
    )

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO amn_afrad(
            user_id,
            sessions_count,
            notes
        )
        VALUES(%s,%s,%s)
        RETURNING id
        """,
        (
            u["id"],
            context.user_data["afrad_sessions"],
            update.message.text,
        ),
    )

    row_id = cur.fetchone()["id"]

    conn.commit()

    cur.close()
    conn.close()

    body = (
        f"👤 المستخدم: {u['username']}\n"
        f"📂 القسم: "
        f"{u['section_name'] or u['module']}\n"
        f"👮 الجلسات المنجزة: "
        f"{context.user_data['afrad_sessions']}\n"
        f"📝 الملاحظات: {update.message.text}"
    )

    await finish_question(
        update,
        context,
        "👮 أمن الأفراد",
        body,
        "amn_afrad",
        row_id,
    )

    return ConversationHandler.END


# ============================================================
# أمن العاملين
# ============================================================

async def alamlen_rounds(update, context):
    try:
        value = int(
            update.message.text.strip()
        )
    except ValueError:
        await update.message.reply_text(
            "❌ أرسل عدد الجولات كرقم:"
        )
        return ALAMLEN_ROUNDS

    context.user_data["rounds"] = value

    await update.message.reply_text(
        "📍 أرسل مكان الجولة:"
    )

    return ALAMLEN_LOCATION


async def alamlen_location(update, context):
    context.user_data[
        "location"
    ] = update.message.text

    await update.message.reply_text(
        "📝 أرسل الملاحظات أو -:"
    )

    return ALAMLEN_NOTES


async def alamlen_notes(update, context):
    u = get_user(
        update.effective_user.id
    )

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO amn_alamlen(
            user_id,
            rounds_count,
            location,
            notes
        )
        VALUES(%s,%s,%s,%s)
        RETURNING id
        """,
        (
            u["id"],
            context.user_data["rounds"],
            context.user_data["location"],
            update.message.text,
        ),
    )

    row_id = cur.fetchone()["id"]

    conn.commit()

    cur.close()
    conn.close()

    body = (
        f"👤 المستخدم: {u['username']}\n"
        f"📂 القسم: "
        f"{u['section_name'] or u['module']}\n"
        f"🚔 عدد الجولات: "
        f"{context.user_data['rounds']}\n"
        f"📍 المكان: "
        f"{context.user_data['location']}\n"
        f"📝 الملاحظات: {update.message.text}"
    )

    await finish_question(
        update,
        context,
        "🚔 أمن العاملين",
        body,
        "amn_alamlen",
        row_id,
    )

    return ConversationHandler.END


# ============================================================
# قسم عام
# ============================================================

async def generic_text(update, context):
    u = get_user(
        update.effective_user.id
    )

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO generic_entries(
            user_id,
            module,
            content
        )
        VALUES(%s,%s,%s)
        RETURNING id
        """,
        (
            u["id"],
            u["module"],
            update.message.text,
        ),
    )

    row_id = cur.fetchone()["id"]

    conn.commit()

    cur.close()
    conn.close()

    body = (
        f"👤 المستخدم: {u['username']}\n"
        f"📂 القسم: "
        f"{u['section_name'] or u['module']}\n"
        f"📝 البيان:\n"
        f"{update.message.text}"
    )

    await finish_question(
        update,
        context,
        f"📝 {u['section_name'] or u['module']}",
        body,
        "generic_entries",
        row_id,
    )

    return ConversationHandler.END


# ============================================================
# إلغاء
# ============================================================

async def cancel(update, context):
    context.user_data.pop(
        "pending_submission",
        None,
    )

    await update.message.reply_text(
        "↩️ تم إلغاء العملية.\n\n"
        "اضغط /start للبدء."
    )

    return ConversationHandler.END


# ============================================================
# Health server لـ Render
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )

        self.end_headers()

        self.wfile.write(
            b"OK"
        )

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(
        os.getenv(
            "PORT",
            "10000",
        )
    )

    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        HealthHandler,
    )

    Thread(
        target=server.serve_forever,
        daemon=True,
    ).start()

    log.info(
        "Health server listening on port %s",
        port,
    )


# ============================================================
# main
# ============================================================

def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN غير موجود في Render Environment Variables."
        )

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL غير موجود في Render Environment Variables."
        )

    start_health_server()

    init_db()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(setup_bot_commands)
        .build()
    )

    # ========================================================
    # تسجيل الدخول
    # ========================================================

    login_conv = ConversationHandler(
        entry_points=[
            CommandHandler(
                "login",
                login_start,
            ),
            CallbackQueryHandler(
                public_callback,
                pattern=r"^public:login$",
            ),
        ],
        states={
            LOGIN_USERNAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    login_username,
                )
            ],
            LOGIN_PASSWORD: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    login_password,
                )
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel,
            )
        ],
        allow_reentry=True,
    )

    # ========================================================
    # إنشاء الحساب
    # ========================================================

    create_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                admin_create_start,
                pattern=r"^m:create$",
            )
        ],
        states={
            CREATE_SECTION: [
                CallbackQueryHandler(
                    admin_create_section,
                    pattern=r"^create_section:",
                ),
                CallbackQueryHandler(
                    add_section_start,
                    pattern=r"^m:add_section$",
                ),
            ],
            CREATE_USERNAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    admin_create_username,
                )
            ],
            CREATE_PASSWORD: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    admin_create_password,
                )
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel,
            )
        ],
        allow_reentry=True,
    )

    # ========================================================
    # إضافة قسم
    # ========================================================

    add_section_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                add_section_start,
                pattern=r"^m:add_section$",
            )
        ],
        states={
            ADD_SECTION_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_section_name,
                )
            ],
            ADD_SECTION_CODE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_section_code,
                )
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel,
            )
        ],
    )

    # ========================================================
    # تعديل كلمة المرور
    # ========================================================

    edit_account_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                admin_edit_start,
                pattern=r"^m:edit$",
            ),
            CallbackQueryHandler(
                admin_edit_account_select,
                pattern=r"^edit_account:\d+$",
            ),
        ],
        states={
            EDIT_ACCOUNT_SELECT: [
                CallbackQueryHandler(
                    admin_edit_account_select,
                    pattern=r"^edit_account:\d+$",
                )
            ],
            EDIT_ACCOUNT_PASSWORD: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    admin_edit_account_password,
                )
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel,
            )
        ],
        allow_reentry=True,
    )

    # ========================================================
    # الديوان
    # ========================================================

    dewan_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                dewan_start,
                pattern=r"^u:dewan:(in|out)$",
            )
        ],
        states={

            DEWAN_RECIPIENT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    dewan_recipient,
                )
            ],

            DEWAN_BOOK_NO: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    dewan_book_number,
                )
            ],

            DEWAN_REQUIRED: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    dewan_required,
                )
            ],

            DEWAN_COMPLETED: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    dewan_completed,
                )
            ],

            DEWAN_NOT_COMPLETED: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    dewan_not_completed,
                )
            ],

            DEWAN_REASON: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    dewan_reason,
                )
            ],
        },

        fallbacks=[
            CommandHandler(
                "cancel",
                cancel,
            )
        ],

        allow_reentry=True,
    )

    # ========================================================
    # المستأجرين
    # ========================================================

    tenant_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                user_callback,
                pattern=r"^u:tenants$",
            )
        ],
        states={
            TENANT_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    tenant_name,
                )
            ],
            TENANT_NATIONALITY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    tenant_nationality,
                )
            ],
            TENANT_PROPERTY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    tenant_property,
                )
            ],
            TENANT_PHONE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    tenant_phone,
                )
            ],
            TENANT_NOTES: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    tenant_notes,
                )
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel,
            )
        ],
    )

    # ========================================================
    # عربي / أجنبي
    # ========================================================

    mig_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                user_callback,
                pattern=r"^u:migrants$",
            )
        ],
        states={
            MIG_PROVINCE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    mig_province,
                )
            ],
            MIG_ARAB: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    mig_arab,
                )
            ],
            MIG_FOREIGN: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    mig_foreign,
                )
            ],
            MIG_STATUS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    mig_status,
                )
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel,
            )
        ],
    )

    # ========================================================
    # التقارير
    # ========================================================

    rep_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                user_callback,
                pattern=r"^u:reports$",
            )
        ],
        states={
            REP_REQUIRED: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    rep_required,
                )
            ],
            REP_COMPLETED: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    rep_completed,
                )
            ],
            REP_IMPOSSIBLE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    rep_impossible,
                )
            ],
            REP_NOTES: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    rep_notes,
                )
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel,
            )
        ],
    )

    # ========================================================
    # أمن الأفراد
    # ========================================================

    afrad_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                user_callback,
                pattern=r"^u:afrad$",
            )
        ],
        states={
            AFRAD_SESSIONS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    afrad_sessions,
                )
            ],
            AFRAD_NOTES: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    afrad_notes,
                )
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel,
            )
        ],
    )

    # ========================================================
    # أمن العاملين
    # ========================================================

    alamlen_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                user_callback,
                pattern=r"^u:alamlen$",
            )
        ],
        states={
            ALAMLEN_ROUNDS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    alamlen_rounds,
                )
            ],
            ALAMLEN_LOCATION: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    alamlen_location,
                )
            ],
            ALAMLEN_NOTES: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    alamlen_notes,
                )
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel,
            )
        ],
    )

    # ========================================================
    # قسم عام جديد
    # ========================================================

    generic_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                user_callback,
                pattern=r"^u:generic$",
            )
        ],
        states={
            GENERIC_TEXT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    generic_text,
                )
            ]
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel,
            )
        ],
    )

    # ========================================================
    # التعميم
    # ========================================================

    broadcast_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                broadcast_start,
                pattern=r"^m:broadcast$",
            )
        ],
        states={
            BROADCAST_TEXT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    broadcast_send,
                )
            ]
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel,
            )
        ],
    )

    # ========================================================
    # ترتيب الـ handlers مهم
    # ========================================================

    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    app.add_handler(login_conv)

    app.add_handler(
        CommandHandler(
            "logout",
            logout,
        )
    )

    app.add_handler(create_conv)
    app.add_handler(add_section_conv)
    app.add_handler(edit_account_conv)

    app.add_handler(broadcast_conv)

    app.add_handler(dewan_conv)
    app.add_handler(tenant_conv)
    app.add_handler(mig_conv)
    app.add_handler(rep_conv)
    app.add_handler(afrad_conv)
    app.add_handler(alamlen_conv)
    app.add_handler(generic_conv)

    # ========================================================
    # تأكيد إرسال البيانات
    # مهم: يجب أن يكون قبل user_callback العام
    # ========================================================

    app.add_handler(
        CallbackQueryHandler(
            finish_callback,
            pattern=r"^finish:",
        )
    )

    # ========================================================
    # المدير
    # ========================================================

    app.add_handler(
        CallbackQueryHandler(
            admin_menu_callback,
            pattern=r"^admin:menu$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_users,
            pattern=r"^m:users$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_user_action,
            pattern=r"^m:user:\d+$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_toggle_one,
            pattern=r"^m:toggleone:\d+$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_reports,
            pattern=r"^m:reports$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_report_section,
            pattern=r"^m:reportsection:\d+$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_report_one,
            pattern=r"^m:reportone:\d+$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_sessions,
            pattern=r"^m:sessions$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_sections,
            pattern=r"^m:sections$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            public_callback,
            pattern=r"^public:info$",
        )
    )

    # ========================================================
    # user callbacks
    # ========================================================

    app.add_handler(
        CallbackQueryHandler(
            user_callback,
            pattern=r"^u:",
        )
    )

    log.info(
        "Starting Telegram bot..."
    )

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
