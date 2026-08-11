from datetime import datetime
import json
import os
import sqlite3
import telebot
from telebot import types

# === قراءة التوكن والآيدي من ملف الويب (config.json) ===
CONFIG_FILE = "config.json"


def load_config():
  if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
      return json.load(f)
  return {}


config = load_config()
TOKEN = config.get("token", "")
ADMIN_ID = int(config.get("admin_id", 0))

if not TOKEN or ADMIN_ID == 0:
  print(
      "⚠️ تنبيه: يرجى التأكد من حفظ التوكن وآيدي المدير من خلال صفحة الويب في ملف"
      " config.json"
  )

bot = telebot.TeleBot(TOKEN) if TOKEN else None


# === إعداد قاعدة البيانات ===
def init_db():
  conn = sqlite3.connect("hotel_system.db")
  cursor = conn.cursor()

  # جدول الفنادق والحسابات
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS hotels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            username TEXT UNIQUE,
            password TEXT,
            status TEXT DEFAULT 'active',
            telegram_id INTEGER DEFAULT 0
        )
    """)

  # جدول البريد (وارد وصادر)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS mail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hotel_name TEXT,
            message TEXT,
            type TEXT,
            date TEXT
        )
    """)

  # إضافة الفنادق الافتراضية إذا كان الجدول فارغاً
  cursor.execute("SELECT COUNT(*) FROM hotels")
  if cursor.fetchone()[0] == 0:
    default_hotels = [
        "قرطبة",
        "النور",
        "النيل",
        "سرمدا",
        "الحميدية",
        "برج التجارة",
        "دريم لاند",
    ]
    for h in default_hotels:
      cursor.execute(
          "INSERT INTO hotels (name, username, password, status) VALUES (?,"
          " ?, ?, ?)",
          (h, f"user_{h}", "123456", "active"),
      )

  conn.commit()
  conn.close()


init_db()


# === صفحة الترحيب والتحقق من الصلاحيات ===
@bot.message_handler(commands=["start"])
def send_welcome(message):
  user_id = message.from_user.id

  # آية قرآنية وسلام وترحيب
  welcome_text = (
      "**السلام عليكم ورحمة الله وبركاته**\n\n"
      "﴿ *وَقُلِ اعْمَلُوا فَسَيَرَى اللَّهُ عَمَلَكُمْ وَرَسُولَهُ وَالْمُؤْمِنُونَ* ﴾\n\n"
      "مرحباً بك، **معكم قسم معلومات الفنادق** 🏨\n"
      "نظام الإدارة والخدمات الفندقية المتكامل."
  )

  # تحقق هل المستخدم هو المدير (مأخوذ من إعدادات الويب)
  if user_id == ADMIN_ID:
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(
            "🏢 إدارة الفنادق", callback_data="admin_hotels"
        ),
        types.InlineKeyboardButton("👥 إدارة الجلسات", callback_data="admin_sessions"),
        types.InlineKeyboardButton("📬 البريد (وارد وصادر)", callback_data="admin_mail"),
        types.InlineKeyboardButton("📊 التقارير", callback_data="admin_reports"),
    )
    bot.send_message(message.chat.id, welcome_text, parse_mode="Markdown")
    bot.send_message(
        message.chat.id,
        "**أهلاً بك يا سيادة المدير، تفضل لوحة التحكم الخاصة بك:**",
        reply_markup=markup,
        parse_mode="Markdown",
    )
    return

  # التحقق مما إذا كان المستخدم فندقاً مسجلاً ودخل مسبقاً
  conn = sqlite3.connect("hotel_system.db")
  cursor = conn.cursor()
  cursor.execute(
      "SELECT name, status FROM hotels WHERE telegram_id = ?", (user_id,)
  )
  hotel = cursor.fetchone()
  conn.close()

  if hotel:
    hotel_name, status = hotel
    if status == "disabled":
      bot.send_message(
          message.chat.id,
          "❌ عذراً، تم تعطيل حسابك أو طردك من قبل الإدارة.",
          parse_mode="Markdown",
      )
      return

    # لوحة الفندق المسجل
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("📤 إرسال رسالة للإدارة", callback_data="hotel_send_mail"),
        types.InlineKeyboardButton("🚪 تسجيل خروج", callback_data="hotel_logout"),
    )
    bot.send_message(message.chat.id, welcome_text, parse_mode="Markdown")
    bot.send_message(
        message.chat.id,
        f"مرحباً بك في لوحة فندق: **{hotel_name}**",
        reply_markup=markup,
        parse_mode="Markdown",
    )
  else:
    # مطالبة بتسجيل الدخول للفندق
    bot.send_message(message.chat.id, welcome_text, parse_mode="Markdown")
    msg = bot.send_message(
        message.chat.id,
        "🔐 يرجى إرسال اسم المستخدم وكلمة المرور الخاصة بفندقك بهذا الشكل:\n`اسم_المستخدم كلمة_المرور`",
        parse_mode="Markdown",
    )
    bot.register_next_step_handler(msg, process_hotel_login)


# === معالجة تسجيل دخول الفندق ===
def process_hotel_login(message):
  try:
    data = message.text.split()
    if len(data) < 2:
      bot.send_message(
          message.chat.id,
          "⚠️ الصيغة غير صحيحة. أرسل هكذا: `username password`",
          parse_mode="Markdown",
      )
      return

    username, password = data[0], data[1]
    user_id = message.from_user.id

    conn = sqlite3.connect("hotel_system.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, name, status FROM hotels WHERE username = ? AND password = ?",
        (username, password),
    )
    hotel = cursor.fetchone()

    if hotel:
      hotel_id, hotel_name, status = hotel
      if status == "disabled":
        bot.send_message(
            message.chat.id,
            "❌ هذا الحساب معطل من قبل الإدارة.",
            parse_mode="Markdown",
        )
        conn.close()
        return

      # تثبيت الجلسة وربط الـ ID بالبوت
      cursor.execute(
          "UPDATE hotels SET telegram_id = ? WHERE id = ?", (user_id, hotel_id)
      )
      conn.commit()
      conn.close()

      bot.send_message(
          message.chat.id,
          f"✅ تم تسجيل الدخول بنجاح لفندق **{hotel_name}**.\nستبقى مسجلاً في البوت حتى تقوم بتسجيل الخروج أو يتم طردك من الإدارة.",
          parse_mode="Markdown",
      )
    else:
      bot.send_message(
          message.chat.id,
          "❌ اسم المستخدم أو كلمة المرور غير صحيحة.",
          parse_mode="Markdown",
      )
      conn.close()
  except Exception as e:
    bot.send_message(message.chat.id, f"حدث خطأ: {e}")


# === إدارة أزرار لوحة تحكم المدير ===
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
  user_id = call.from_user.id

  if call.data == "admin_hotels":
    conn = sqlite3.connect("hotel_system.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name, username, password, status FROM hotels")
    hotels = cursor.fetchall()
    conn.close()

    text = "🏢 **قائمة الفنادق المسجلة:**\n\n"
    for h in hotels:
      text += (
          f"📌 الفندق: **{h[0]}**\n👤 المستخدم: `{h[1]}`\n🔑 المرور: `{h[2]}`\nحالة"
          f" الحساب: {h[3]}\n-------------------\n"
      )

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("➕ إضافة فندق جديد", callback_data="add_hotel"),
        types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_back"),
    )
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown",
    )

  elif call.data == "add_hotel":
    msg = bot.send_message(
        call.message.chat.id,
        "أدخل بيانات الفندق الجديد بهذا الشكل:\n`اسم_الفندق اسم_المستخدم كلمة_المرور`",
        parse_mode="Markdown",
    )
    bot.register_next_step_handler(msg, save_new_hotel)

  elif call.data == "admin_sessions":
    conn = sqlite3.connect("hotel_system.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, status, telegram_id FROM hotels")
    hotels = cursor.fetchall()
    conn.close()

    text = "👥 **إدارة جلسات الفنادق:**\nاختر فندقاً للتحكم بحالته (تعطيل/تفعيل/طرد):\n\n"
    markup = types.InlineKeyboardMarkup(row_width=1)
    for h in hotels:
      markup.add(
          types.InlineKeyboardButton(
              f"فندق: {h[1]} ({h[2]})", callback_data=f"manage_session_{h[0]}"
          )
      )
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_back"))
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown",
    )

  elif call.data.startswith("manage_session_"):
    hotel_id = call.data.split("_")[2]
    conn = sqlite3.connect("hotel_system.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name, status FROM hotels WHERE id = ?", (hotel_id,))
    h = cursor.fetchone()
    conn.close()

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(
            "✅ تفعيل", callback_data=f"set_status_{hotel_id}_active"
        ),
        types.InlineKeyboardButton(
            "⚠️ تعطيل", callback_data=f"set_status_{hotel_id}_disabled"
        ),
        types.InlineKeyboardButton(
            "🚫 طرد (إلغاء الجلسة)",
            callback_data=f"set_status_{hotel_id}_kick",
        ),
        types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_sessions"),
    )
    bot.edit_message_text(
        f"التحكم بالفندق: **{h[0]}**\nالحالة الحالية: {h[1]}",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown",
    )

  elif call.data.startswith("set_status_"):
    _, _, hotel_id, action = call.data.split("_")
    conn = sqlite3.connect("hotel_system.db")
    cursor = conn.cursor()
    if action == "kick":
      cursor.execute(
          "UPDATE hotels SET status = 'active', telegram_id = 0 WHERE id = ?",
          (hotel_id,),
      )
      msg_text = "🚫 تم طرد الفندق وإلغاء جلسته بنجاح."
    else:
      cursor.execute(
          "UPDATE hotels SET status = ? WHERE id = ?", (action, hotel_id)
      )
      msg_text = f"✅ تم تحديث حالة الفندق إلى: {action}"
    conn.commit()
    conn.close()
    bot.answer_callback_query(call.id, msg_text)

  elif call.data == "admin_mail":
    conn = sqlite3.connect("hotel_system.db")
    cursor = conn.cursor()
    cursor.execute("SELECT hotel_name, message, type, date FROM mail")
    mails = cursor.fetchall()
    conn.close()

    text = "📬 **سجل البريد (الوارد والصادر):**\n\n"
    if not mails:
      text += "لا توجد رسائل حالياً."
    for m in mails:
      text += (
          f"🏨 الفندق: {m[0]}\nنوع البريد: {m[2]}\nالرسالة:"
          f" {m[1]}\nالتاريخ: {m[3]}\n-------------------\n"
      )

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_back"))
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown",
    )

  elif call.data == "admin_reports":
    today = datetime.now().strftime("%Y-%m-%d")
    current_month = datetime.now().strftime("%Y-%m")

    conn = sqlite3.connect("hotel_system.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM hotels")
    total_hotels = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM hotels WHERE telegram_id != 0")
    active_sessions = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COUNT(*) FROM mail WHERE date LIKE ?", (f"{current_month}%",)
    )
    monthly_mails = cursor.fetchone()[0]
    conn.close()

    text = (
        f"📊 **التقارير والإحصائيات:**\n\n"
        f"📅 **التقرير اليومي ({today}):**\n"
        f"- إجمالي الفنادق: {total_hotels}\n"
        f"- الجلسات النشطة حالياً: {active_sessions}\n\n"
        f"📈 **التقرير الشهري ({current_month}):**\n"
        f"- إجمالي مراسلات البريد لهذا الشهر: {monthly_mails}\n"
    )

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_back"))
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown",
    )

  elif call.data == "admin_back":
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(
            "🏢 إدارة الفنادق", callback_data="admin_hotels"
        ),
        types.InlineKeyboardButton("👥 إدارة الجلسات", callback_data="admin_sessions"),
        types.InlineKeyboardButton("📬 البريد (وارد وصادر)", callback_data="admin_mail"),
        types.InlineKeyboardButton("📊 التقارير", callback_data="admin_reports"),
    )
    bot.edit_message_text(
        "**لوحة التحكم الرئيسية للمدير:**",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown",
    )

  elif call.data == "hotel_send_mail":
    msg = bot.send_message(
        call.message.chat.id, "أرسل محتوى الرسالة ليتم إرسالها إلى إدارة البوت (وارد وصادر):"
    )
    bot.register_next_step_handler(msg, save_hotel_mail)

  elif call.data == "hotel_logout":
    user_id = call.from_user.id
    conn = sqlite3.connect("hotel_system.db")
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE hotels SET telegram_id = 0 WHERE telegram_id = ?", (user_id,)
    )
    conn.commit()
    conn.close()
    bot.answer_callback_query(call.id, "تم تسجيل الخروج بنجاح.")
    bot.send_message(
        call.message.chat.id,
        "🚪 لقد قمت بتسجيل الخروج. أرسل /start لتسجيل الدخول مرة أخرى.",
    )


# === حفظ فندق جديد من قبل المدير ===
def save_new_hotel(message):
  try:
    data = message.text.split()
    if len(data) < 3:
      bot.send_message(
          message.chat.id,
          "⚠️ الصيغة خطأ. أرسل هكذا: `اسم_الفندق اسم_المستخدم كلمة_المرور`",
          parse_mode="Markdown",
      )
      return

    name, username, password = data[0], data[1], data[2]
    conn = sqlite3.connect("hotel_system.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO hotels (name, username, password, status) VALUES (?,"
        " ?, ?, 'active')",
        (name, username, password),
    )
    conn.commit()
    conn.close()
    bot.send_message(
        message.chat.id,
        f"✅ تم إضافة الفندق **{name}** بنجاح.",
        parse_mode="Markdown",
    )
  except Exception as e:
    bot.send_message(
        message.chat.id,
        f"❌ حدث خطأ (قد يكون اسم المستخدم موجوداً مسبقاً): {e}",
    )


# === حفظ رسائل البريد للفنادق ===
def save_hotel_mail(message):
  user_id = message.from_user.id
  text = message.text
  date_now = datetime.now().strftime("%Y-%m-%d %H:%M")

  conn = sqlite3.connect("hotel_system.db")
  cursor = conn.cursor()
  cursor.execute("SELECT name FROM hotels WHERE telegram_id = ?", (user_id,))
  hotel = cursor.fetchone()

  if hotel:
    hotel_name = hotel[0]
    cursor.execute(
        "INSERT INTO mail (hotel_name, message, type, date) VALUES (?, ?, ?,"
        " ?)",
        (hotel_name, text, "وارد", date_now),
    )
    conn.commit()
    conn.close()
    bot.send_message(
        message.chat.id, "✅ تم إرسال الرسالة إلى إدارة البوت بنجاح (بريد وارد)."
    )
  else:
    conn.close()
    bot.send_message(message.chat.id, "❌ حدث خطأ، يرجى تسجيل الدخول أولاً.")


# === تشغيل البوت ===
if __name__ == "__main__":
  if bot:
    print("البوت يعمل الآن بنجاح...")
    bot.infinity_polling()
  else:
    print(
        "❌ خطأ: يرجى التأكد من إنشاء ملف config.json وتعبئته بالتوكن والآيدي من"
        " صفحة الويب."
    )
